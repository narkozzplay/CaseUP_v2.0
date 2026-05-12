import shutil
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan
from modules.v2.store.models import ThemeStoreListing, UserThemePurchase

from .dashboard_menu import build_dashboard_menu, ensure_dashboard_menu_items
from .context_processors import system_settings
from .models import DashboardMenuItem, SiteConfiguration, ThemeProfile, ProjectVersion, EnvironmentModuleState, ModuleRelease, ExternalServiceIntegration
from .models import ModuleCompatibility
from .services import (
    bulk_ensure_module_releases_compatibility_for_target_version,
    bulk_update_module_compatibility_to_target_version,
    bootstrap_versioning,
    get_active_theme,
    get_control_center_summary,
    get_current_project_version,
    get_external_service_health,
    get_runtime_environment,
)
from .module_registry import MODULE_REGISTRY
from .services import get_module_state_map, get_site_configuration, update_module_states
from .theme_plugins import discover_runtime_themes

User = get_user_model()


class SystemConfigurationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _make_user(self, username="plain_user", role=UserRole.USER):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = role
        profile.save(update_fields=["role"])
        return user

    def _make_admin(self, username="admin_system"):
        return self._make_user(username=username, role=UserRole.ADMIN)

    def test_site_configuration_bootstraps_defaults(self):
        config = get_site_configuration()
        self.assertEqual(config.site_name, "CaseUP")
        self.assertIn("steam_catalog", get_module_state_map())

    def test_regular_user_cannot_open_system_settings(self):
        user = self._make_user("plain_system_user")
        self.client.login(username=user.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_open_system_settings_page(self):
        admin = self._make_admin("admin_settings_open")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System settings")
        self.assertContains(response, "Site configuration")
        self.assertContains(response, "Module flags")

    def test_system_settings_includes_external_integrations_section(self):
        admin = self._make_admin("admin_settings_integrations")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "External API integrations")
        self.assertContains(response, "Steam API key")
        self.assertContains(response, "Telegram bot token")

    def test_system_settings_template_renders_all_registered_modules(self):
        admin = self._make_admin("admin_settings_modules")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)

        for definition in MODULE_REGISTRY.values():
            self.assertContains(response, definition.label)

        self.assertContains(response, "Commercial access for")
        self.assertContains(response, "Subscriptions")

    def test_store_module_is_registered_and_disabled_by_default(self):
        bootstrap_versioning()
        state_map = get_module_state_map()
        self.assertIn("store", state_map)
        self.assertFalse(state_map["store"])

    def test_system_settings_shows_module_release_metadata(self):
        admin = self._make_admin("admin_settings_release_meta")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))

        stable_release = ModuleRelease.objects.filter(module_key="steam_catalog").first()
        self.assertIsNotNone(stable_release)
        self.assertContains(response, "Current release:")
        self.assertContains(response, stable_release.version)
        self.assertContains(response, stable_release.channel)
        self.assertContains(response, f"{get_runtime_environment().upper()} runtime release:")

    def test_cannot_enable_dependent_module_without_dependency(self):
        update_module_states({"steam_catalog": False, "steam_trading": False})

        with self.assertRaises(ValidationError):
            update_module_states({"steam_trading": True})

    def test_disabled_module_returns_404(self):
        user = self._make_user("user_disabled_module")
        SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            item_type=SteamItem.ItemType.CASE,
        )
        update_module_states(
            {
                "steam_catalog": False,
                "steam_trading": False,
            }
        )

        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Steam Catalog", status_code=404)
        self.assertNotContains(response, "Module is disabled.", status_code=404)

    def test_disabled_module_page_shows_environment_reason_from_notes(self):
        user = self._make_user("user_disabled_reason")
        update_module_states({"steam_catalog": False})
        env = get_runtime_environment()
        env_state = EnvironmentModuleState.objects.get(environment=env, module_key="steam_catalog")
        env_state.notes = "Плановые работы: миграция каталога."
        env_state.save(update_fields=["notes", "updated_at"])

        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Плановые работы: миграция каталога.", status_code=404)

    def test_admin_can_save_system_configuration(self):
        admin = self._make_admin("admin_system_save")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.post(
            reverse("system_config:settings"),
            {
                "site_name": "CaseUP Prod",
                "site_currency_code": "RUB",
                "site_currency_rate": "1.000000",
                "support_email": "support@example.com",
                "public_base_url": "https://caseup.example.com",
                "maintenance_mode_enabled": "on",
                "maintenance_message": "Maint",
                "registration_enabled": "on",
                "steam_health_mode": "auto",
                "faceit_health_mode": "auto",
                "telegram_health_mode": "auto",
                "module__steam_catalog": "on",
                "module__farm": "on",
                "module__tradehub": "on",
                "module__steam_trading": "on",
                "module__celery_tasks": "on",
                "module__tg_notifier": "on",
                "module__subscriptions": "on",
            },
        )
        self.assertEqual(response.status_code, 302)

        config = SiteConfiguration.objects.get(singleton_key=1)
        self.assertEqual(config.site_name, "CaseUP Prod")
        self.assertTrue(config.maintenance_mode_enabled)
        self.assertEqual(str(config.site_currency_rate), "1.000000")
        self.assertTrue(get_module_state_map()["tg_notifier"])

    def test_admin_can_save_external_api_integrations(self):
        admin = self._make_admin("admin_integrations_save")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.post(
            reverse("system_config:settings"),
            {
                "site_name": "CaseUP",
                "site_currency_code": "RUB",
                "site_currency_rate": "1.000000",
                "support_email": "support@example.com",
                "public_base_url": "https://caseup.example.com",
                "maintenance_message": "Maint",
                "registration_enabled": "on",
                "steam_api_key": "steam-key-123",
                "faceit_api_key": "faceit-key-123",
                "telegram_bot_token": "bot-token-123",
                "steam_health_mode": "force_up",
                "faceit_health_mode": "force_degraded",
                "telegram_health_mode": "force_down",
                "degraded_stub_message": "Degraded stub",
                "down_stub_message": "Down stub",
                "module__steam_catalog": "on",
                "module__farm": "on",
                "module__tradehub": "on",
                "module__steam_trading": "on",
                "module__celery_tasks": "on",
                "module__tg_notifier": "on",
                "module__subscriptions": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        integration = ExternalServiceIntegration.objects.get(singleton_key=1)
        self.assertEqual(integration.steam_api_key, "steam-key-123")
        self.assertEqual(integration.faceit_health_mode, "force_degraded")
        self.assertEqual(integration.telegram_health_mode, "force_down")

    def test_external_service_health_respects_force_modes(self):
        integration = ExternalServiceIntegration.objects.create(
            steam_api_key="steam-key",
            faceit_api_key="faceit-key",
            telegram_bot_token="telegram-key",
            steam_health_mode="force_up",
            faceit_health_mode="force_degraded",
            telegram_health_mode="force_down",
            degraded_stub_message="Degraded",
            down_stub_message="Down",
        )
        health = {item.key: item for item in get_external_service_health()}
        self.assertEqual(health["steam"].status, "up")
        self.assertEqual(health["faceit"].message, "Degraded")
        self.assertEqual(health["telegram"].message, "Down")

    def test_partial_save_returns_theme_headers_for_css_hot_swap(self):
        admin = self._make_admin("admin_partial_theme_swap")
        bootstrap_versioning()
        default_theme = ThemeProfile.objects.get(key="default")
        midnight_theme = ThemeProfile.objects.get(key="midnight")
        update_module_states({"midnight_theme": True})
        config = get_site_configuration()
        config.active_theme = default_theme
        config.save(update_fields=["active_theme", "updated_at"])

        self.client.login(username=admin.username, password="StrongPass123")
        response = self.client.post(
            reverse("system_config:settings"),
            {
                "site_name": "CaseUP",
                "site_currency_code": "RUB",
                "site_currency_rate": "1.000000",
                "support_email": "support@example.com",
                "public_base_url": "https://caseup.example.com",
                "maintenance_message": "Maint",
                "registration_enabled": "on",
                "current_project_version": str(config.current_project_version_id or ""),
                "active_theme": str(midnight_theme.id),
                "module__steam_catalog": "on",
                "module__farm": "on",
                "module__tradehub": "on",
                "module__steam_trading": "on",
                "module__celery_tasks": "on",
                "module__subscriptions": "on",
                "module__midnight_theme": "on",
            },
            HTTP_X_DASHBOARD_PARTIAL="1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Dashboard-Partial"], "1")
        self.assertEqual(response["X-Dashboard-Theme-Mode"], "css")
        self.assertIn("css/themes/midnight.css", response["X-Dashboard-Theme-Css-Url"])

    def test_midnight_theme_falls_back_to_default_when_plugin_disabled(self):
        bootstrap_versioning()
        midnight_theme = ThemeProfile.objects.get(key="midnight")
        default_theme = ThemeProfile.objects.get(key="default")
        config = get_site_configuration()
        config.active_theme = midnight_theme
        config.save(update_fields=["active_theme", "updated_at"])

        update_module_states({"midnight_theme": False})
        active_theme = get_active_theme()
        self.assertIsNotNone(active_theme)
        self.assertEqual(active_theme.key, default_theme.key)

    def test_admin_can_toggle_module_live_endpoint(self):
        admin = self._make_admin("admin_toggle_module_live")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.post(reverse("system_config:toggle_module_state", kwargs={"module_key": "store"}), {"enabled": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "module_key": "store",
                "enabled": True,
                "message": "Module 'Store' enabled.",
            },
        )
        self.assertTrue(get_module_state_map()["store"])

    def test_midnight_live_toggle_warning_and_confirm_hook_present_when_active(self):
        admin = self._make_admin("admin_midnight_warning_hook")
        bootstrap_versioning()
        update_module_states({"midnight_theme": True})
        config = get_site_configuration()
        config.active_theme = ThemeProfile.objects.get(key="midnight")
        config.save(update_fields=["active_theme", "updated_at"])

        self.client.login(username=admin.username, password="StrongPass123")
        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disable Midnight Theme Plugin? Active theme will switch to default.")
        self.assertContains(response, "data-module-live-toggle")
        self.assertContains(response, 'data-module-toggle-confirm="Disable Midnight Theme Plugin? Active theme will switch to default."', count=1)

    def test_midnight_theme_uses_topnav_shell_layout(self):
        admin = self._make_admin("admin_midnight_shell")
        self.client.login(username=admin.username, password="StrongPass123")
        bootstrap_versioning()
        midnight = ThemeProfile.objects.get(key="midnight")
        config = get_site_configuration()
        config.active_theme = midnight
        config.save(update_fields=["active_theme", "updated_at"])
        update_module_states({"midnight_theme": True})

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "midnight-topnav")
        self.assertContains(response, "dashboard-shell--midnight")

    def test_midnight_theme_renders_versioning_page_with_topnav_shell(self):
        admin = self._make_admin("admin_midnight_versioning_shell")
        self.client.login(username=admin.username, password="StrongPass123")
        bootstrap_versioning()
        midnight = ThemeProfile.objects.get(key="midnight")
        config = get_site_configuration()
        config.active_theme = midnight
        config.save(update_fields=["active_theme", "updated_at"])
        update_module_states({"midnight_theme": True})

        response = self.client.get(reverse("system_config:versioning"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "midnight-topnav")
        self.assertContains(response, "dashboard-shell--midnight")

    def test_store_menu_item_visible_only_when_store_enabled(self):
        user = self._make_user("menu_store_user")
        self.client.login(username=user.username, password="StrongPass123")
        update_module_states({"store": False})
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertNotContains(response, reverse("store:storefront"))

        update_module_states({"store": True})
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertContains(response, reverse("store:storefront"))
        
    def test_invalid_dependency_post_shows_error_and_does_not_save_conflict(self):
        admin = self._make_admin("admin_invalid_dependency")
        self.client.login(username=admin.username, password="StrongPass123")

        update_module_states(
            {
                "steam_catalog": False,
                "steam_trading": False,
                "farm": True,
                "tradehub": True,
                "celery_tasks": True,
                "tg_notifier": False,
            }
        )

        response = self.client.post(
            reverse("system_config:settings"),
            {
                "site_name": "CaseUP Prod",
                "site_currency_code": "RUB",
                "site_currency_rate": "1.000000",
                "support_email": "support@example.com",
                "public_base_url": "https://caseup.example.com",
                "maintenance_message": "Maint",
                "registration_enabled": "on",
                "module__farm": "on",
                "module__tradehub": "on",
                "module__celery_tasks": "on",
                "module__tg_notifier": "",
                "module__subscriptions": "on",
                "module__steam_trading": "on",
                # steam_catalog intentionally omitted -> False
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нельзя включить модуль")
        self.assertContains(response, "Steam Catalog")

    def test_disabling_dependency_cascades_to_dependent_module(self):
        admin = self._make_admin("admin_cascade_disable")
        self.client.login(username=admin.username, password="StrongPass123")
    
        update_module_states(
            {
                "steam_catalog": True,
                "steam_trading": True,
                "farm": True,
                "tradehub": True,
                "celery_tasks": True,
                "tg_notifier": False,
            }
        )
    
        response = self.client.post(
            reverse("system_config:settings"),
            {
                "site_name": "CaseUP",
                "site_currency_code": "RUB",
                "site_currency_rate": "1.000000",
                "support_email": "support@example.com",
                "public_base_url": "https://caseup.example.com",
                "maintenance_message": "Maint",
                "registration_enabled": "on",
                "module__farm": "on",
                "module__tradehub": "on",
                "module__celery_tasks": "on",
                "module__subscriptions": "on",
                "module__steam_trading": "on",
                # steam_catalog intentionally omitted -> False
            },
            follow=True,
        )
    
        self.assertEqual(response.status_code, 200)
        state_map = get_module_state_map()
        self.assertFalse(state_map["steam_catalog"])
        self.assertFalse(state_map["steam_trading"])
        self.assertContains(response, "Каскадно выключены зависимые модули")
        self.assertContains(response, "Steam Trading")

    def test_update_module_states_cascades_disable_for_existing_dependents(self):
        update_module_states({"steam_catalog": True, "steam_trading": True})

        update_module_states({"steam_catalog": False})

        state_map = get_module_state_map()
        self.assertFalse(state_map["steam_catalog"])
        self.assertFalse(state_map["steam_trading"])

    def test_oauth_debug_endpoint_is_not_public(self):
        response = self.client.get("/oauth-debug/")
        self.assertEqual(response.status_code, 404)

    def test_maintenance_mode_blocks_plain_user(self):
        user = self._make_user(username="plain_maint")
        config = get_site_configuration()
        config.maintenance_mode_enabled = True
        config.save(update_fields=["maintenance_mode_enabled", "updated_at"])

        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "технические работы", status_code=503)

    def test_maintenance_mode_allows_i18n_login_path(self):
        config = get_site_configuration()
        config.maintenance_mode_enabled = True
        config.save(update_fields=["maintenance_mode_enabled", "updated_at"])

        response = self.client.get("/en/auth/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Steam")

    def test_admin_can_open_i18n_control_center_route_even_when_module_disabled(self):
        admin = self._make_admin("admin_i18n_control_center")
        update_module_states({"celery_tasks": False})
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get("/en/control-center/celery-tasks/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Celery Tasks")


    def test_admin_can_activate_theme_with_redirect(self):
        admin = self._make_admin("admin_activate_theme")
        bootstrap_versioning()
        midnight = ThemeProfile.objects.get(key="midnight")
        update_module_states({"midnight_theme": True})

        self.client.login(username=admin.username, password="StrongPass123")
        response = self.client.post(
            reverse("system_config:activate_theme"),
            {"theme": midnight.id, "next_url": reverse("system_config:versioning")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Midnight Neon")
        self.assertContains(response, "активирована")
        self.assertEqual(get_site_configuration().active_theme_id, midnight.id)



class ThemeVersioningTests(TestCase):
    def test_bootstrap_versioning_seeds_default_and_midnight_themes(self):
        bootstrap_versioning()

        themes = set(ThemeProfile.objects.values_list("key", flat=True))

        self.assertIn("default", themes)
        self.assertIn("midnight", themes)

    def test_midnight_theme_is_visible_in_dev_versioning_page(self):
        user = User.objects.create_user(username="theme_admin", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        update_module_states({"midnight_theme": True})

        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("system_config:versioning"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Midnight Neon")
        self.assertContains(response, "themes/midnight")


class SystemBootstrapIsolationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_system_context_processor_does_not_bootstrap_versioning(self):
        ProjectVersion.objects.all().delete()
        ThemeProfile.objects.all().delete()

        request = self.factory.get("/")
        request.user = AnonymousUser()
        context = system_settings(request)

        self.assertIn("site_settings", context)
        self.assertEqual(ProjectVersion.objects.count(), 0)
        self.assertEqual(ThemeProfile.objects.count(), 0)

    def test_control_center_summary_does_not_bootstrap_versioning(self):
        ProjectVersion.objects.all().delete()
        ThemeProfile.objects.all().delete()

        summary = get_control_center_summary()

        self.assertIn("enabled_count", summary)
        self.assertEqual(ProjectVersion.objects.count(), 0)
        self.assertEqual(ThemeProfile.objects.count(), 0)


class RuntimeThemeDiscoveryTests(TestCase):
    def test_runtime_theme_discovery_includes_demo_package(self):
        discovered = discover_runtime_themes(refresh=True)
        self.assertIn("steam_catalog_price_graph_plus", discovered)
        self.assertEqual(discovered["steam_catalog_price_graph_plus"].name, "Улучшенный график цен в стим каталоге")

    def test_invalid_theme_manifest_is_ignored(self):
        themes_root = Path(settings.BASE_DIR) / "modules" / "plugins" / "themes"
        broken_dir = themes_root / "broken_theme_manifest"
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "theme.json").write_text("{invalid json", encoding="utf-8")
        try:
            discovered = discover_runtime_themes(refresh=True)
            self.assertNotIn("broken_theme_manifest", discovered)
        finally:
            shutil.rmtree(broken_dir, ignore_errors=True)
            discover_runtime_themes(refresh=True)


class UserRuntimeThemeResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="theme_context_user", password="StrongPass123")
        self.demo_listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")
        update_module_states({"store": True})

    def test_unpurchased_private_theme_does_not_override_scope_css(self):
        self.user.profile_v2.theme_preferences = {"steam_catalog": "steam_catalog_price_graph_plus"}
        self.user.profile_v2.save(update_fields=["theme_preferences", "updated_at"])
        request = RequestFactory().get("/steam-catalog/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(namespace="steam_catalog", view_name="steam_catalog:list")

        context = system_settings(request)

        self.assertEqual(context["runtime_theme_css_files"], tuple())

    def test_purchased_theme_overrides_scope_css_for_owner(self):
        plan = SubscriptionPlan.objects.filter(
            family=SubscriptionPlan.Family.SITE,
            level=1,
            is_active=True,
        ).first()
        self.assertIsNotNone(plan)
        assign_user_subscription_plan(user=self.user, plan=plan)

        UserThemePurchase.objects.create(
            user=self.user,
            listing=self.demo_listing,
            theme_key=self.demo_listing.theme_key,
            amount_rub=self.demo_listing.price_rub,
        )
        self.user.profile_v2.theme_preferences = {"steam_catalog": self.demo_listing.theme_key}
        self.user.profile_v2.save(update_fields=["theme_preferences", "updated_at"])

        request = RequestFactory().get("/steam-catalog/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(namespace="steam_catalog", view_name="steam_catalog:list")

        context = system_settings(request)

        self.assertIn("steam_catalog_price_graph_plus/css/steam_catalog_price_graph_plus.css", context["runtime_theme_css_files"])


class DashboardMenuBuilderTests(TestCase):
    def _make_user(self, username="plain_dashboard", role=UserRole.USER):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = role
        profile.save(update_fields=["role"])
        return user

    def test_dashboard_menu_bootstraps_defaults(self):
        ensure_dashboard_menu_items()
        self.assertTrue(DashboardMenuItem.objects.filter(key="farm_drops").exists())
        self.assertTrue(DashboardMenuItem.objects.filter(key="steam_catalog").exists())
        self.assertEqual(DashboardMenuItem.objects.get(key="web_constructor").url_name, "web_constructor:builder")

    def test_disabled_menu_item_is_hidden(self):
        ensure_dashboard_menu_items()
        user = self._make_user(username="farmer_menu_hidden", role=UserRole.FARMER)
        DashboardMenuItem.objects.filter(key="farm_drops").update(is_enabled=False)

        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Дропы")

    def test_farm_and_steam_sections_merge_into_investments_group(self):
        ensure_dashboard_menu_items()
        user = self._make_user(username="farmer_menu_merge", role=UserRole.FARMER)
    
        site_plan = SubscriptionPlan.objects.filter(
            family=SubscriptionPlan.Family.SITE,
            level=1,
            is_active=True,
        ).first()
        self.assertIsNotNone(site_plan)
    
        assign_user_subscription_plan(user=user, plan=site_plan)
    
        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))
    
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Инвестиции")
        menu_groups = build_dashboard_menu(response.wsgi_request)
        self.assertTrue(any(group["key"] == DashboardMenuItem.Group.INVESTMENTS for group in menu_groups))
        self.assertFalse(any(group["key"] == DashboardMenuItem.Group.FARM for group in menu_groups))
        self.assertFalse(any(group["key"] == DashboardMenuItem.Group.STEAM for group in menu_groups))

    @override_settings(CASEUP_LAB_BASE_URL="http://lib.caseup.localhost:8000", ALLOWED_HOSTS=["testserver", "caseup.localhost", "lib.caseup.localhost"])
    def test_web_constructor_menu_item_points_to_lab_host(self):
        ensure_dashboard_menu_items()
        user = self._make_user(username="menu_lab_link", role=UserRole.ADMIN)
        self.client.login(username=user.username, password="StrongPass123")

        response = self.client.get(reverse("frontend:dashboard_preview"), HTTP_HOST="caseup.localhost:8000")

        self.assertEqual(response.status_code, 200)
        menu_groups = build_dashboard_menu(response.wsgi_request)
        web_constructor_item = next(
            item
            for group in menu_groups
            for item in group["items"]
            if item.key == "web_constructor"
        )
        self.assertEqual(web_constructor_item.url, "http://lib.caseup.localhost:8000/constructor/builder/")

    def test_web_constructor_menu_item_hidden_when_module_disabled(self):
        ensure_dashboard_menu_items()
        user = self._make_user(username="menu_web_constructor_disabled", role=UserRole.ADMIN)
        self.client.login(username=user.username, password="StrongPass123")

        update_module_states({"web_constructor": False})
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        menu_groups = build_dashboard_menu(response.wsgi_request)
        self.assertFalse(
            any(
                item.key == "web_constructor"
                for group in menu_groups
                for item in group["items"]
            )
        )

    def test_steam_catalog_menu_item_hidden_when_module_disabled(self):
        ensure_dashboard_menu_items()
        user = self._make_user(username="menu_steam_catalog_disabled", role=UserRole.ADMIN)
        self.client.login(username=user.username, password="StrongPass123")

        update_module_states({"steam_catalog": False})
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        menu_groups = build_dashboard_menu(response.wsgi_request)
        self.assertFalse(
            any(
                item.key == "steam_catalog"
                for group in menu_groups
                for item in group["items"]
            )
        )


class SystemVersioningTests(TestCase):
    def setUp(self):
        bootstrap_versioning()

    def test_versioning_bootstrap_creates_current_project_version_and_theme(self):
        config = get_site_configuration()
        self.assertIsNotNone(get_current_project_version())
        self.assertIsNotNone(get_active_theme())
        self.assertTrue(ThemeProfile.objects.filter(is_default=True).exists())
        self.assertIsNotNone(config.current_project_version)

    def test_admin_can_open_versioning_page(self):
        user = User.objects.create_user(username="admin_versioning", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        self.client.login(username=user.username, password="StrongPass123")
        response = self.client.get(reverse("system_config:versioning"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Versions & Themes")
        self.assertContains(response, "Module release matrix")

    def test_environment_module_states_seeded_for_all_environments(self):
        states = EnvironmentModuleState.objects.filter(module_key="steam_catalog")
        self.assertEqual(states.count(), 3)
        self.assertTrue(states.filter(environment="dev").exists())
        self.assertTrue(states.filter(environment="prod").exists())
        self.assertTrue(states.filter(environment="test").exists())


class SystemBulkCompatibilityServicesTests(TestCase):
    def setUp(self):
        self.version_1 = ProjectVersion.objects.create(version="0.1.0", version_order=10, title="0.1.0")
        self.version_2 = ProjectVersion.objects.create(version="0.2.0", version_order=20, title="0.2.0")
        self.version_3 = ProjectVersion.objects.create(version="0.3.0", version_order=30, title="0.3.0")

        self.release_main = ModuleRelease.objects.create(
            module_key="steam_catalog",
            version="1.0.0",
            version_order=100,
            channel=ModuleRelease.Channel.STABLE,
            title="Steam Catalog 1.0.0",
            is_default=False,
            is_active=True,
        )
        self.release_conflict = ModuleRelease.objects.create(
            module_key="farm",
            version="1.0.0",
            version_order=100,
            channel=ModuleRelease.Channel.STABLE,
            title="Farm 1.0.0",
            is_default=False,
            is_active=True,
        )
        self.release_unchanged = ModuleRelease.objects.create(
            module_key="tradehub",
            version="1.0.0",
            version_order=100,
            channel=ModuleRelease.Channel.STABLE,
            title="TradeHub 1.0.0",
            is_default=False,
            is_active=True,
        )

    def test_bulk_update_module_compatibility_to_target_version_handles_skips(self):
        compat_update = ModuleCompatibility.objects.create(
            module_release=self.release_main,
            project_version_from=self.version_1,
            project_version_to=self.version_1,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )
        compat_invalid = ModuleCompatibility.objects.create(
            module_release=self.release_main,
            project_version_from=self.version_3,
            project_version_to=self.version_3,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )
        compat_conflict = ModuleCompatibility.objects.create(
            module_release=self.release_conflict,
            project_version_from=self.version_1,
            project_version_to=self.version_1,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )
        ModuleCompatibility.objects.create(
            module_release=self.release_conflict,
            project_version_from=self.version_1,
            project_version_to=self.version_2,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )
        compat_unchanged = ModuleCompatibility.objects.create(
            module_release=self.release_unchanged,
            project_version_from=self.version_1,
            project_version_to=self.version_2,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )

        summary = bulk_update_module_compatibility_to_target_version(
            compatibilities=ModuleCompatibility.objects.filter(
                pk__in=[compat_update.pk, compat_invalid.pk, compat_conflict.pk, compat_unchanged.pk]
            ),
            target_version=self.version_2,
        )

        compat_update.refresh_from_db()
        compat_invalid.refresh_from_db()
        compat_conflict.refresh_from_db()
        compat_unchanged.refresh_from_db()

        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.skipped_invalid_range, 1)
        self.assertEqual(summary.skipped_conflict, 1)
        self.assertEqual(summary.skipped_unchanged, 1)
        self.assertEqual(compat_update.project_version_to_id, self.version_2.id)
        self.assertEqual(compat_invalid.project_version_to_id, self.version_3.id)
        self.assertEqual(compat_conflict.project_version_to_id, self.version_1.id)

    def test_bulk_ensure_module_releases_compatibility_for_target_version_is_idempotent(self):
        ModuleCompatibility.objects.create(
            module_release=self.release_main,
            project_version_from=self.version_2,
            project_version_to=self.version_2,
            compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
        )

        summary = bulk_ensure_module_releases_compatibility_for_target_version(
            module_releases=ModuleRelease.objects.filter(pk__in=[self.release_main.pk, self.release_conflict.pk]),
            target_version=self.version_2,
        )

        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.existing, 1)
        self.assertTrue(
            ModuleCompatibility.objects.filter(
                module_release=self.release_conflict,
                project_version_from=self.version_2,
                project_version_to=self.version_2,
            ).exists()
        )
