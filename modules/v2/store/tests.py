from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan
from modules.v2.system.module_plugins import (
    discover_runtime_modules,
    get_runtime_module,
    is_runtime_module_compatible,
)
from modules.v2.system.services import update_module_states

from .models import (
    StoreBundleItem,
    StoreProductEntitlement,
    StoreProductReview,
    ThemeStoreListing,
    UserBundlePurchase,
    UserStorePurchase,
    UserThemePurchase,
)
from .presentation import (
    build_review_presenter,
    build_star_rating_data,
    get_product_preview_url,
    normalize_rating_to_half,
)
from .services import (
    DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
    STEAM_CATALOG_DETAIL_MODE_SLOT,
    STEAM_CATALOG_DETAIL_CONTENT_SLOT,
    ThemePurchaseError,
    activate_module_slot,
    activate_theme_globally,
    can_manage_store_listing,
    get_store_card_for_user,
    deactivate_module_slot,
    deactivate_theme_globally,
    moderate_listing,
    purchase_bundle_with_balance,
    purchase_store_listing_with_balance,
    purchase_theme_with_balance,
    resolve_store_module_slot_template,
    resolve_store_product_access_state,
    submit_listing_for_moderation,
    submit_store_review,
)
from PIL import Image

User = get_user_model()


def _build_test_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=(40, 72, 132)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_1X1_BYTES = _build_test_png_bytes()


def _grant_steam_catalog_access(user) -> None:
    plan = SubscriptionPlan.objects.get(level=1)
    assign_user_subscription_plan(user=user, plan=plan)


class StorePresentationPatchTests(TestCase):
    def setUp(self):
        update_module_states({"store": True, "subscriptions": True, "steam_catalog": True})
        self.user = User.objects.create_user(username="store_presenter_user", password="StrongPass123")
        self.listing = ThemeStoreListing.objects.create(
            key="store-presenter-module",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Presenter module",
            short_description="Presenter module short",
            full_description="Presenter module full",
            author="CaseUP",
            price_rub=Decimal("0.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
            cover_image="",
        )

    def test_product_preview_url_fallback_and_cover_priority(self):
        fallback_url = get_product_preview_url(self.listing)
        self.assertTrue(fallback_url.endswith("img/placeholders/no_image.svg"))

        self.listing.cover_image = "https://cdn.example.com/store/preview.webp"
        explicit_url = get_product_preview_url(self.listing)
        self.assertEqual(explicit_url, "https://cdn.example.com/store/preview.webp")

    def test_average_rating_normalization_floor_to_half(self):
        self.assertEqual(normalize_rating_to_half(Decimal("4.7")), Decimal("4.5"))
        self.assertEqual(normalize_rating_to_half(Decimal("4.2")), Decimal("4"))
        self.assertEqual(normalize_rating_to_half(Decimal("5.0")), Decimal("5"))

    def test_star_rating_data_builds_expected_states(self):
        rating_data = build_star_rating_data(Decimal("4.7"), precision=1)
        self.assertEqual(rating_data["value_label"], "4.7")
        self.assertEqual(rating_data["normalized_value"], Decimal("4.5"))
        self.assertEqual(tuple(rating_data["states"]), ("full", "full", "full", "full", "half"))

    def test_review_presenter_uses_steam_display_name_role_prefix_and_badge(self):
        self.user.profile_v2.role = UserRole.ADMIN
        self.user.profile_v2.steam_personaname = "Steam Display Name"
        self.user.profile_v2.steam_avatar = ""
        self.user.profile_v2.save(update_fields=["role", "steam_personaname", "steam_avatar"])

        review = StoreProductReview.objects.create(
            listing=self.listing,
            user=self.user,
            rating=5,
            comment="Great module",
            improvement_suggestion="Add more charts",
            access_source_snapshot=StoreProductReview.AccessSource.PURCHASE,
        )
        presenter = build_review_presenter(review)

        self.assertEqual(presenter["display_name"], "Steam Display Name")
        self.assertEqual(presenter["role_prefix"], "[ADMIN]")
        self.assertEqual(presenter["access_badges"], ("Куплено",))
        self.assertTrue(str(presenter["avatar_url"]).endswith("img/placeholders/no_image.svg"))


class StoreRuntimeModuleDiscoveryTests(TestCase):
    def test_runtime_module_discovery_includes_demo_package(self):
        discovered = discover_runtime_modules(refresh=True)
        self.assertIn("steam_update_plugin_price_graphics", discovered)
        self.assertIn("steam_catalog_v2", discovered)

    def test_runtime_module_discovery_ignores_broken_manifest(self):
        modules_root = Path(settings.BASE_DIR) / "modules" / "plugins" / "modules"
        broken_dir = modules_root / "broken_module_manifest"
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "module.json").write_text("{invalid json", encoding="utf-8")
        try:
            discovered = discover_runtime_modules(refresh=True)
            self.assertNotIn("broken_module_manifest", discovered)
        finally:
            shutil.rmtree(broken_dir, ignore_errors=True)
            discover_runtime_modules(refresh=True)

    def test_runtime_module_compatibility_contract(self):
        module = get_runtime_module("steam_update_plugin_price_graphics")
        self.assertIsNotNone(module)
        self.assertTrue(is_runtime_module_compatible(module))
        self.assertFalse(is_runtime_module_compatible(module, project_version="9.9.9"))

    def test_steam_runtime_modules_are_compatible_with_project_032(self):
        price_graphics = get_runtime_module("steam_update_plugin_price_graphics")
        catalog_v2 = get_runtime_module("steam_catalog_v2")

        self.assertIsNotNone(price_graphics)
        self.assertIsNotNone(catalog_v2)
        self.assertTrue(is_runtime_module_compatible(price_graphics, project_version="0.3.2"))
        self.assertTrue(is_runtime_module_compatible(catalog_v2, project_version="0.3.2"))


class StoreFoundationFlowTests(TestCase):
    def setUp(self):
        update_module_states({"store": True, "subscriptions": True, "steam_catalog": True})

        self.user = User.objects.create_user(username="store_user", password="StrongPass123")
        self.subscriber = User.objects.create_user(username="store_subscriber", password="StrongPass123")
        self.admin = User.objects.create_user(username="store_admin", password="StrongPass123")
        self.admin.profile_v2.role = UserRole.ADMIN
        self.admin.profile_v2.save(update_fields=["role"])

        self.author = User.objects.create_user(username="store_author", password="StrongPass123")
        self.author.profile_v2.is_store_author = True
        self.author.profile_v2.save(update_fields=["is_store_author"])
        self.no_access_user = User.objects.create_user(username="store_no_access", password="StrongPass123")
        _grant_steam_catalog_access(self.user)

        self.theme_listing = ThemeStoreListing.objects.create(
            key="store-theme-demo",
            category=ThemeStoreListing.Category.THEMES,
            theme_key="steam_catalog_price_graph_plus",
            title="Theme demo",
            short_description="Theme demo",
            full_description="Theme demo",
            author="CaseUP",
            price_rub=Decimal("100.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )
        self.module_listing = ThemeStoreListing.objects.create(
            key="store-module-demo",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Module demo",
            short_description="Module demo",
            full_description="Module demo",
            author="CaseUP",
            price_rub=Decimal("120.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
            is_included_in_store_subscription=True,
        )
        self.mode_module_listing = ThemeStoreListing.objects.create(
            key="store-module-mode-demo",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_catalog_v2",
            title="Module mode demo",
            short_description="Module mode demo",
            full_description="Module mode demo",
            author="CaseUP",
            price_rub=Decimal("130.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )
        self.bundle_listing = ThemeStoreListing.objects.create(
            key="store-bundle-demo",
            category=ThemeStoreListing.Category.BUNDLES,
            bundle_key="store_bundle_demo",
            title="Bundle demo",
            short_description="Bundle demo",
            full_description="Bundle demo",
            author="CaseUP",
            price_rub=Decimal("180.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )
        StoreBundleItem.objects.create(
            bundle=self.bundle_listing,
            product_type=StoreBundleItem.ProductType.THEME,
            product_key=self.theme_listing.theme_key,
            sort_order=10,
        )
        StoreBundleItem.objects.create(
            bundle=self.bundle_listing,
            product_type=StoreBundleItem.ProductType.MODULE,
            product_key=self.module_listing.module_key,
            sort_order=20,
        )

    def test_purchase_theme_flow_not_broken_creates_legacy_purchase(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))

        purchase_theme_with_balance(user=self.user, listing_id=self.theme_listing.id)

        self.assertTrue(UserThemePurchase.objects.filter(user=self.user, theme_key=self.theme_listing.theme_key).exists())
        self.assertTrue(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                listing=self.theme_listing,
                source=StoreProductEntitlement.Source.PURCHASE,
            ).exists()
        )

    def test_module_purchase_flow_creates_purchase_access_source(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))

        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        state = resolve_store_product_access_state(user=self.user, listing=self.module_listing)

        self.assertEqual(state.access_source, "purchase")
        self.assertTrue(state.is_purchased)

    def test_repeated_store_listing_purchase_does_not_charge_twice(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("300.00"))

        first_purchase = purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        self.user.profile_v2.refresh_from_db()
        balance_after_first = self.user.profile_v2.balance_coins
        second_purchase = purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        self.user.profile_v2.refresh_from_db()

        self.assertEqual(second_purchase.id, first_purchase.id)
        self.assertEqual(self.user.profile_v2.balance_coins, balance_after_first)
        self.assertEqual(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.STORE_PURCHASE).count(), 1)
        self.assertEqual(UserStorePurchase.objects.filter(user=self.user, listing=self.module_listing).count(), 1)
        self.assertEqual(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                listing=self.module_listing,
                source=StoreProductEntitlement.Source.PURCHASE,
            ).count(),
            1,
        )

    def test_existing_store_purchase_returns_without_charge(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("300.00"))
        existing_purchase = UserStorePurchase.objects.create(
            user=self.user,
            listing=self.module_listing,
            category=self.module_listing.category,
            product_key=self.module_listing.runtime_key,
            amount_rub=self.module_listing.price_rub,
        )

        returned_purchase = purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        self.user.profile_v2.refresh_from_db()

        self.assertEqual(returned_purchase.id, existing_purchase.id)
        self.assertEqual(self.user.profile_v2.balance_coins, Decimal("300.00"))
        self.assertFalse(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.STORE_PURCHASE).exists())
        self.assertTrue(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                listing=self.module_listing,
                source=StoreProductEntitlement.Source.PURCHASE,
            ).exists()
        )

    def test_existing_entitlement_returns_without_charge(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("300.00"))
        StoreProductEntitlement.objects.create(
            user=self.user,
            listing=self.module_listing,
            source=StoreProductEntitlement.Source.PURCHASE,
        )

        purchase = purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        self.user.profile_v2.refresh_from_db()

        self.assertIsNotNone(purchase.id)
        self.assertEqual(purchase.amount_rub, Decimal("0.00"))
        self.assertEqual(self.user.profile_v2.balance_coins, Decimal("300.00"))
        self.assertFalse(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.STORE_PURCHASE).exists())

    def test_bundle_purchase_flow_grants_entitlements(self):
        adjust_balance(user=self.user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))

        purchase_bundle_with_balance(user=self.user, listing_id=self.bundle_listing.id)

        self.assertTrue(UserBundlePurchase.objects.filter(user=self.user, bundle_key=self.bundle_listing.bundle_key).exists())
        self.assertTrue(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                listing=self.theme_listing,
                source=StoreProductEntitlement.Source.BUNDLE_PURCHASE,
            ).exists()
        )
        self.assertTrue(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                listing=self.module_listing,
                source=StoreProductEntitlement.Source.BUNDLE_PURCHASE,
            ).exists()
        )

    def test_repeated_bundle_purchase_does_not_charge_twice(self):
        adjust_balance(user=self.user, delta=Decimal("500.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("500.00"))

        first_purchase = purchase_bundle_with_balance(user=self.user, listing_id=self.bundle_listing.id)
        self.user.profile_v2.refresh_from_db()
        balance_after_first = self.user.profile_v2.balance_coins
        second_purchase = purchase_bundle_with_balance(user=self.user, listing_id=self.bundle_listing.id)
        self.user.profile_v2.refresh_from_db()

        self.assertEqual(second_purchase.id, first_purchase.id)
        self.assertEqual(self.user.profile_v2.balance_coins, balance_after_first)
        self.assertEqual(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.BUNDLE_PURCHASE).count(), 1)
        self.assertEqual(UserBundlePurchase.objects.filter(user=self.user, bundle_key=self.bundle_listing.bundle_key).count(), 1)
        self.assertEqual(
            StoreProductEntitlement.objects.filter(
                user=self.user,
                source=StoreProductEntitlement.Source.BUNDLE_PURCHASE,
            ).count(),
            2,
        )

    def test_insufficient_balance_does_not_create_purchase_or_entitlement(self):
        with self.assertRaises(ThemePurchaseError):
            purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)

        self.assertFalse(UserStorePurchase.objects.filter(user=self.user, listing=self.module_listing).exists())
        self.assertFalse(StoreProductEntitlement.objects.filter(user=self.user, listing=self.module_listing).exists())
        self.assertFalse(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.STORE_PURCHASE).exists())

    def test_store_access_resolver_subscription_only_for_included_products(self):
        level_5 = SubscriptionPlan.objects.get(code="level_5_pro")
        assign_user_subscription_plan(user=self.subscriber, plan=level_5)

        with_sub = resolve_store_product_access_state(user=self.subscriber, listing=self.module_listing)
        self.module_listing.is_included_in_store_subscription = False
        self.module_listing.save(update_fields=["is_included_in_store_subscription", "updated_at"])
        without_sub = resolve_store_product_access_state(user=self.subscriber, listing=self.module_listing)

        self.assertEqual(with_sub.access_source, "subscription")
        self.assertEqual(without_sub.access_source, "none")

    def test_access_source_priority_prefers_purchase_over_subscription(self):
        level_5 = SubscriptionPlan.objects.get(code="level_5_pro")
        assign_user_subscription_plan(user=self.user, plan=level_5)
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)

        state = resolve_store_product_access_state(user=self.user, listing=self.module_listing)
        self.assertEqual(state.access_source, "purchase")

    def test_activation_and_deactivation_flows(self):
        adjust_balance(user=self.user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))
        purchase_theme_with_balance(user=self.user, listing_id=self.theme_listing.id)
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)

        activate_theme_globally(user=self.user, listing_id=self.theme_listing.id)
        activate_module_slot(user=self.user, listing_id=self.module_listing.id, slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT)

        self.user.profile_v2.refresh_from_db()
        self.assertEqual(self.user.profile_v2.theme_preferences.get("global"), self.theme_listing.theme_key)
        self.assertEqual(
            self.user.profile_v2.module_preferences.get(STEAM_CATALOG_DETAIL_CONTENT_SLOT),
            self.module_listing.module_key,
        )

        deactivate_theme_globally(user=self.user)
        deactivate_module_slot(user=self.user, slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT)

        self.user.profile_v2.refresh_from_db()
        self.assertNotIn("global", self.user.profile_v2.theme_preferences)
        self.assertNotIn(STEAM_CATALOG_DETAIL_CONTENT_SLOT, self.user.profile_v2.module_preferences)

    def test_steam_catalog_slot_template_resolver_with_and_without_store(self):
        adjust_balance(user=self.user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        activate_module_slot(user=self.user, listing_id=self.module_listing.id, slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT)

        template_with_module = resolve_store_module_slot_template(
            user=self.user,
            slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT,
            request_scope="steam_catalog",
            default_template=DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
        )
        self.assertEqual(
            template_with_module,
            "steam_update_plugin_price_graphics/steam_catalog/detail_content.html",
        )

        update_module_states({"store": False})
        template_fallback = resolve_store_module_slot_template(
            user=self.user,
            slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT,
            request_scope="steam_catalog",
            default_template=DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
        )
        self.assertEqual(template_fallback, DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE)

    def test_steam_catalog_mode_slot_template_resolver_with_v2_module(self):
        adjust_balance(user=self.user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.mode_module_listing.id)
        activate_module_slot(user=self.user, listing_id=self.mode_module_listing.id, slot=STEAM_CATALOG_DETAIL_MODE_SLOT)

        template_with_module = resolve_store_module_slot_template(
            user=self.user,
            slot=STEAM_CATALOG_DETAIL_MODE_SLOT,
            request_scope="steam_catalog",
            default_template=DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
        )
        self.assertEqual(
            template_with_module,
            "steam_catalog_v2/steam_catalog/detail_content.html",
        )

    def test_store_disabled_returns_none_access_even_if_user_owned(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)

        update_module_states({"store": False})
        state = resolve_store_product_access_state(user=self.user, listing=self.module_listing)

        self.assertEqual(state.access_source, "none")
        self.assertFalse(state.can_activate)

    def test_target_scope_gating_blocks_purchase_without_steam_catalog_access(self):
        adjust_balance(user=self.no_access_user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))

        with self.assertRaises(ThemePurchaseError):
            purchase_store_listing_with_balance(user=self.no_access_user, listing_id=self.module_listing.id)

    def test_target_scope_gating_blocks_bundle_purchase_without_steam_catalog_access(self):
        adjust_balance(user=self.no_access_user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))

        with self.assertRaises(ThemePurchaseError):
            purchase_bundle_with_balance(user=self.no_access_user, listing_id=self.bundle_listing.id)

    def test_target_scope_gating_marks_store_card_unavailable(self):
        card = get_store_card_for_user(user=self.no_access_user, listing=self.module_listing)

        self.assertFalse(card["has_target_scope_access"])
        self.assertFalse(card["can_purchase"])
        self.assertIn("Steam Catalog", card["target_scope_access_message"])

    def test_review_requires_valid_access(self):
        with self.assertRaises(ThemePurchaseError):
            submit_store_review(
                user=self.user,
                listing=self.theme_listing,
                rating=5,
                comment="No access",
                improvement_suggestion="",
            )

        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))
        purchase_theme_with_balance(user=self.user, listing_id=self.theme_listing.id)
        review = submit_store_review(
            user=self.user,
            listing=self.theme_listing,
            rating=4,
            comment="Nice",
            improvement_suggestion="Add more variants",
        )

        self.assertEqual(review.access_source_snapshot, StoreProductReview.AccessSource.PURCHASE)
        self.assertEqual(StoreProductReview.objects.filter(listing=self.theme_listing).count(), 1)

    def test_author_permissions_and_moderation_workflow(self):
        listing = ThemeStoreListing.objects.create(
            key="author-module-draft",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Author module",
            short_description="Author module",
            full_description="Author module",
            author="Author",
            price_rub=Decimal("50.00"),
            status=ThemeStoreListing.Status.DRAFT,
            is_public=False,
            submitted_by=self.author,
        )

        self.assertTrue(can_manage_store_listing(user=self.author, listing=listing))
        self.assertFalse(can_manage_store_listing(user=self.user, listing=listing))

        submit_listing_for_moderation(user=self.author, listing=listing)
        listing.refresh_from_db()
        self.assertEqual(listing.status, ThemeStoreListing.Status.PENDING_MODERATION)

        moderate_listing(user=self.admin, listing=listing, action="approve", comment="Looks good")
        listing.refresh_from_db()
        self.assertEqual(listing.status, ThemeStoreListing.Status.PUBLISHED)
        self.assertEqual(listing.moderated_by_id, self.admin.id)

class StoreViewsDegradeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="store_view_user", password="StrongPass123")
        self.client.login(username="store_view_user", password="StrongPass123")

    def test_storefront_gracefully_degrades_when_store_disabled(self):
        update_module_states({"store": False})
        response = self.client.get("/store/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Module disabled", status_code=404)


class StoreViewsPatchUxTests(TestCase):
    def setUp(self):
        update_module_states({"store": True, "subscriptions": True, "steam_catalog": True})
        self.user = User.objects.create_user(username="store_patch_user", password="StrongPass123")
        self.author = User.objects.create_user(username="store_patch_author", password="StrongPass123")
        self.author.profile_v2.is_store_author = True
        self.author.profile_v2.steam_personaname = "Steam Author Display"
        self.author.profile_v2.save(update_fields=["is_store_author", "steam_personaname"])
        _grant_steam_catalog_access(self.user)

        self.client.login(username="store_patch_user", password="StrongPass123")
        self.listing = ThemeStoreListing.objects.create(
            key="store-patch-module",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Patch module",
            short_description="Patch module short",
            full_description="Patch module full",
            author="CaseUP",
            price_rub=Decimal("0.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
            cover_image="",
        )
        self.author_listing = ThemeStoreListing.objects.create(
            key="store-patch-author-module",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Author patch module",
            short_description="Author module short",
            full_description="Author module full",
            author="Author Initial",
            price_rub=Decimal("30.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
            submitted_by=self.author,
        )

    def test_storefront_hides_debug_fields_and_uses_preview_fallback(self):
        response = self.client.get("/store/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.listing.title)
        self.assertContains(response, "img/placeholders/no_image.svg")
        self.assertNotContains(response, "category=")
        self.assertNotContains(response, "runtime_key=")

    def test_storefront_card_buy_action_goes_to_detail_not_purchase_post(self):
        response = self.client.get("/store/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("store:product_detail", args=[self.listing.key]))
        self.assertNotContains(response, reverse("store:purchase_product", args=[self.listing.id]))
        self.assertContains(response, ">Купить</a>", html=False)

    def test_storefront_card_shows_open_for_owned_listing(self):
        purchase_store_listing_with_balance(user=self.user, listing_id=self.listing.id)

        response = self.client.get("/store/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Открыть</a>", html=False)

    def test_product_detail_hides_review_form_without_entitlement(self):
        reviewer = User.objects.create_user(username="reviewer_patch_user", password="StrongPass123")
        reviewer.profile_v2.steam_personaname = "Steam Reviewer"
        reviewer.profile_v2.save(update_fields=["steam_personaname"])
        StoreProductReview.objects.create(
            listing=self.listing,
            user=reviewer,
            rating=4,
            comment="Solid plugin",
            improvement_suggestion="Improve spacing",
            access_source_snapshot=StoreProductReview.AccessSource.SUBSCRIPTION,
        )

        response = self.client.get(f"/store/products/{self.listing.key}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-store-rating-input")
        self.assertContains(response, "Оставить отзыв могут только владельцы товара")
        self.assertContains(response, "Steam Reviewer")
        self.assertContains(response, "[USER]")
        self.assertContains(response, "Что можно улучшить:")
        self.assertNotContains(response, "5/5")
        self.assertNotContains(response, "category=")
        self.assertNotContains(response, "runtime_key=")

    def test_review_post_endpoint_rejects_non_entitled_user(self):
        response = self.client.post(
            reverse("store:add_review", args=[self.listing.id]),
            {
                "rating": "5",
                "comment": "Attempt without entitlement",
                "improvement_suggestion": "",
                "next_url": reverse("store:product_detail", args=[self.listing.key]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Оставлять отзыв можно только")
        self.assertFalse(StoreProductReview.objects.filter(listing=self.listing, user=self.user).exists())

    def test_product_detail_shows_rating_input_for_entitled_user(self):
        purchase_store_listing_with_balance(user=self.user, listing_id=self.listing.id)

        response = self.client.get(reverse("store:product_detail", args=[self.listing.key]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-store-rating-input")

        post_response = self.client.post(
            reverse("store:add_review", args=[self.listing.id]),
            {
                "rating": "4",
                "comment": "Great patch",
                "improvement_suggestion": "More themes",
                "next_url": reverse("store:product_detail", args=[self.listing.key]),
            },
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(StoreProductReview.objects.filter(listing=self.listing, user=self.user).exists())

    def test_management_link_points_to_focused_editor_page(self):
        self.client.login(username="store_patch_author", password="StrongPass123")
        response = self.client.get(reverse("store:product_detail", args=[self.author_listing.key]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("store:author_product_edit", args=[self.author_listing.id]))
        self.assertNotContains(response, "#store-author-product-")

    def test_author_panel_create_form_does_not_expose_editable_author_field(self):
        self.client.login(username="store_patch_author", password="StrongPass123")
        response = self.client.get(reverse("store:author_products"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="author"')
        self.assertContains(response, "Steam Author Display")

    def test_author_product_create_sets_author_from_current_user(self):
        self.client.login(username="store_patch_author", password="StrongPass123")

        response = self.client.post(
            reverse("store:author_product_create"),
            {
                "next_url": reverse("store:author_products"),
                "category": ThemeStoreListing.Category.MODULES,
                "key": "author-created-listing",
                "module_key": "steam_update_plugin_price_graphics",
                "title": "Created by author",
                "short_description": "short",
                "full_description": "full",
                "author": "FORGED AUTHOR",
                "price_rub": "15.00",
                "is_public": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        created = ThemeStoreListing.objects.get(key="author-created-listing")
        self.assertEqual(created.submitted_by_id, self.author.id)
        self.assertEqual(created.author, "Steam Author Display")

    def test_author_product_edit_is_focused_for_specific_listing(self):
        self.client.login(username="store_patch_author", password="StrongPass123")
        another_listing = ThemeStoreListing.objects.create(
            key="store-patch-author-second",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Second author listing",
            short_description="Second",
            full_description="Second",
            author="Second Author",
            price_rub=Decimal("25.00"),
            status=ThemeStoreListing.Status.DRAFT,
            is_public=False,
            submitted_by=self.author,
        )

        response = self.client.get(reverse("store:author_product_edit", args=[self.author_listing.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.author_listing.title)
        self.assertNotContains(response, another_listing.title)
        self.assertContains(response, reverse("store:author_product_update", args=[self.author_listing.id]))

    def test_author_product_update_supports_cover_and_screenshots_upload(self):
        self.client.login(username="store_patch_author", password="StrongPass123")
        media_root = tempfile.mkdtemp(prefix="caseup-store-patch-tests-")
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))

        cover_file = SimpleUploadedFile("cover.png", PNG_1X1_BYTES, content_type="image/png")
        shot_file_1 = SimpleUploadedFile("shot1.png", PNG_1X1_BYTES, content_type="image/png")
        shot_file_2 = SimpleUploadedFile("shot2.png", PNG_1X1_BYTES, content_type="image/png")

        with self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("store:author_product_update", args=[self.author_listing.id]),
                {
                    "next_url": reverse("store:author_product_edit", args=[self.author_listing.id]),
                    "title": self.author_listing.title,
                    "short_description": self.author_listing.short_description,
                    "full_description": self.author_listing.full_description,
                    "price_rub": str(self.author_listing.price_rub),
                    "is_public": "on",
                    "is_included_in_store_subscription": "on",
                    "cover_image_file": cover_file,
                    "screenshots_files": [shot_file_1, shot_file_2],
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200)

            self.author_listing.refresh_from_db()
            self.assertIn("store/products/store-patch-author-module/cover/", self.author_listing.cover_image)
            self.assertEqual(len(self.author_listing.screenshots), 2)
            self.assertTrue(all("store/products/store-patch-author-module/screenshots/" in item for item in self.author_listing.screenshots))

            screenshot_to_remove = self.author_listing.screenshots[0]
            remove_response = self.client.post(
                reverse("store:author_product_update", args=[self.author_listing.id]),
                {
                    "next_url": reverse("store:author_product_edit", args=[self.author_listing.id]),
                    "title": self.author_listing.title,
                    "short_description": self.author_listing.short_description,
                    "full_description": self.author_listing.full_description,
                    "price_rub": str(self.author_listing.price_rub),
                    "is_public": "on",
                    "remove_cover_image": "1",
                    "remove_screenshots": [screenshot_to_remove],
                },
                follow=True,
            )
            self.assertEqual(remove_response.status_code, 200)

            self.author_listing.refresh_from_db()
            self.assertEqual(self.author_listing.cover_image, "")
            self.assertEqual(len(self.author_listing.screenshots), 1)
