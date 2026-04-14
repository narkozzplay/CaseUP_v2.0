from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from modules.v2.profiles.models import UserRole
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
    UserThemePurchase,
)
from .services import (
    DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
    STEAM_CATALOG_DETAIL_CONTENT_SLOT,
    ThemePurchaseError,
    activate_module_slot,
    activate_theme_globally,
    can_manage_store_listing,
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

User = get_user_model()


class StoreRuntimeModuleDiscoveryTests(TestCase):
    def test_runtime_module_discovery_includes_demo_package(self):
        discovered = discover_runtime_modules(refresh=True)
        self.assertIn("steam_update_plugin_price_graphics", discovered)

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

    def test_store_disabled_returns_none_access_even_if_user_owned(self):
        adjust_balance(user=self.user, delta=Decimal("300.00"), reason="topup", amount_snapshot=Decimal("300.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)

        update_module_states({"store": False})
        state = resolve_store_product_access_state(user=self.user, listing=self.module_listing)

        self.assertEqual(state.access_source, "none")
        self.assertFalse(state.can_activate)

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
