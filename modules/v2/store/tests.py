from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.system.services import update_module_states

from .models import ThemeStoreListing, UserThemePurchase
from .services import (
    get_user_runtime_theme_map,
    get_user_theme_options_by_scope,
    purchase_theme_with_balance,
    resolve_user_theme_runtime_state,
)

User = get_user_model()


class StoreThemeFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="theme_buyer", password="StrongPass123")
        update_module_states({"store": True})
        self.demo_listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")

    def test_demo_theme_listing_is_seeded_and_has_runtime_theme_key(self):
        self.assertEqual(self.demo_listing.theme_key, "steam_catalog_price_graph_plus")
        self.assertEqual(self.demo_listing.price_rub, Decimal("1000.00"))

    def test_unbought_private_theme_is_not_available_for_selection(self):
        options = get_user_theme_options_by_scope(self.user)
        steam_catalog_keys = {theme.key for theme in options.get("steam_catalog", [])}
        self.assertNotIn(self.demo_listing.theme_key, steam_catalog_keys)

    def test_purchase_theme_with_balance_grants_runtime_access(self):
        adjust_balance(
            user=self.user,
            delta=Decimal("1500.00"),
            reason="topup",
            amount_snapshot=Decimal("1500.00"),
        )

        purchase_theme_with_balance(user=self.user, listing_id=self.demo_listing.id)

        self.assertTrue(
            UserThemePurchase.objects.filter(
                user=self.user,
                theme_key=self.demo_listing.theme_key,
            ).exists()
        )
        runtime_map = get_user_runtime_theme_map(self.user)
        self.assertIn(self.demo_listing.theme_key, runtime_map)

    def test_bought_theme_appears_in_user_scope_options(self):
        adjust_balance(
            user=self.user,
            delta=Decimal("1500.00"),
            reason="topup",
            amount_snapshot=Decimal("1500.00"),
        )
        purchase_theme_with_balance(user=self.user, listing_id=self.demo_listing.id)

        options = get_user_theme_options_by_scope(self.user)
        steam_catalog_keys = {theme.key for theme in options.get("steam_catalog", [])}
        self.assertIn(self.demo_listing.theme_key, steam_catalog_keys)

    def test_storefront_shows_runtime_unavailable_listing_without_crash(self):
        ThemeStoreListing.objects.create(
            key="broken-theme-link",
            category=ThemeStoreListing.Category.THEMES,
            theme_key="missing_runtime_theme",
            title="Broken runtime demo",
            short_description="Missing runtime package",
            full_description="Missing runtime package",
            author="System",
            price_rub=Decimal("500.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )

        self.client.login(username="theme_buyer", password="StrongPass123")
        response = self.client.get(reverse("store:storefront"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broken runtime demo")
        self.assertContains(response, "Theme package is unavailable")

    def test_purchase_theme_rejects_external_next_url_redirect(self):
        self.client.login(username="theme_buyer", password="StrongPass123")
        response = self.client.post(
            reverse("store:purchase_theme", args=[self.demo_listing.id]),
            {"next_url": "https://evil.example/phish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("store:storefront"))

    def test_missing_runtime_package_for_selected_theme_falls_back_without_crash(self):
        ThemeStoreListing.objects.create(
            key="missing-runtime-selected",
            category=ThemeStoreListing.Category.THEMES,
            theme_key="missing_runtime_theme",
            title="Missing runtime selected",
            short_description="Broken package",
            full_description="Broken package",
            author="System",
            price_rub=Decimal("0.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )
        UserThemePurchase.objects.create(
            user=self.user,
            listing=ThemeStoreListing.objects.get(key="missing-runtime-selected"),
            theme_key="missing_runtime_theme",
            amount_rub=Decimal("0.00"),
        )
        self.user.profile_v2.theme_preferences = {"steam_catalog": "missing_runtime_theme"}
        self.user.profile_v2.save(update_fields=["theme_preferences", "updated_at"])

        runtime_state = resolve_user_theme_runtime_state(user=self.user, request_scope="steam_catalog")
        self.assertEqual(runtime_state.css_files, tuple())


class StoreThemePermissionsTests(TestCase):
    def test_admin_plus_can_see_private_runtime_theme_without_purchase(self):
        admin = User.objects.create_user(username="store_admin", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])

        options = get_user_theme_options_by_scope(admin)
        steam_catalog_keys = {theme.key for theme in options.get("steam_catalog", [])}
        self.assertIn("steam_catalog_price_graph_plus", steam_catalog_keys)
