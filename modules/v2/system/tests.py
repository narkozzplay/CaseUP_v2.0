from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan

from .dashboard_menu import build_dashboard_menu, ensure_dashboard_menu_items
from .models import DashboardMenuItem, SiteConfiguration, ThemeProfile, ProjectVersion, EnvironmentModuleState
from .services import get_active_theme, get_current_project_version, bootstrap_versioning
from .module_registry import MODULE_REGISTRY
from .services import get_module_state_map, get_site_configuration, update_module_states

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
        self.assertContains(response, "System Settings")
        self.assertContains(response, "Site configuration")
        self.assertContains(response, "Module flags")

    def test_system_settings_template_renders_all_registered_modules(self):
        admin = self._make_admin("admin_settings_modules")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 200)

        for definition in MODULE_REGISTRY.values():
            self.assertContains(response, definition.label)

        self.assertContains(response, "Commercial access for")
        self.assertContains(response, "Subscriptions")

    def test_cannot_enable_dependent_module_without_dependency(self):
        update_module_states({"steam_catalog": False, "steam_trading": False})

        with self.assertRaises(ValidationError):
            update_module_states({"steam_trading": True})

    def test_disabled_module_returns_404(self):
        admin = self._make_admin("admin_disabled_module")
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

        self.client.login(username=admin.username, password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))
        self.assertEqual(response.status_code, 404)

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
