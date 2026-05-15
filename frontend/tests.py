from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.store.models import ThemeStoreListing, UserThemePurchase
from modules.v2.system.models import ThemeProfile
from modules.v2.system.services import bootstrap_versioning, get_site_configuration, update_module_states

User = get_user_model()


class FrontendDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _activate_midnight_theme(self):
        bootstrap_versioning()
        update_module_states({"midnight_theme": True})
        config = get_site_configuration()
        config.active_theme = ThemeProfile.objects.get(key="midnight")
        config.save(update_fields=["active_theme", "updated_at"])

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_available_for_authenticated_user(self):
        user = User.objects.create_user(username="user1", password="StrongPass123")
        self.client.login(username="user1", password="StrongPass123")

        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CaseUP")

    def test_profile_smoke_for_ru_and_en_language_paths(self):
        user = User.objects.create_user(username="language_profile_user", password="StrongPass123")
        self.client.login(username="language_profile_user", password="StrongPass123")

        ru_response = self.client.get("/profile/")
        en_response = self.client.get("/en/profile/")

        self.assertEqual(ru_response.status_code, 200)
        self.assertEqual(en_response.status_code, 200)
        self.assertContains(ru_response, 'href="/en/profile/"')
        self.assertContains(en_response, 'href="/profile/"')
        self.assertContains(ru_response, 'data-dashboard-nav="false"')
        self.assertContains(en_response, 'data-dashboard-nav="false"')

    def test_admin_panel_forbidden_for_plain_user(self):
        user = User.objects.create_user(username="user2", password="StrongPass123")
        self.client.login(username="user2", password="StrongPass123")

        response = self.client.get(reverse("frontend:admin_panel"))
        self.assertEqual(response.status_code, 403)

    def test_admin_panel_available_for_admin_role(self):
        user = User.objects.create_user(username="admin1", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        self.client.login(username="admin1", password="StrongPass123")
        response = self.client.get(reverse("frontend:admin_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control Center")

    def test_admin_panel_balances_article_tags_when_optional_cards_disabled(self):
        admin = User.objects.create_user(username="admin_panel_html", password="StrongPass123")
        profile = UserProfile.objects.get(user=admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        update_module_states({"celery_tasks": False, "tg_notifier": False})

        self.client.login(username="admin_panel_html", password="StrongPass123")
        response = self.client.get(reverse("frontend:admin_panel"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertEqual(content.count("<article"), content.count("</article>"))

    def test_admin_panel_hides_disabled_module_blocks(self):
        admin = User.objects.create_user(username="admin_panel_hidden_blocks", password="StrongPass123")
        profile = UserProfile.objects.get(user=admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        update_module_states({"celery_tasks": False, "tg_notifier": False})

        self.client.login(username="admin_panel_hidden_blocks", password="StrongPass123")
        response = self.client.get(reverse("frontend:admin_panel"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("celery_tasks:control_center"))
        self.assertNotContains(response, reverse("tg_notifier:settings"))

    def test_admin_panel_dev_topup_updates_balance(self):
        admin = User.objects.create_user(username="admin2", password="StrongPass123")
        admin_profile = UserProfile.objects.get(user=admin)
        admin_profile.role = UserRole.ADMIN
        admin_profile.save(update_fields=["role"])

        target = User.objects.create_user(username="target1", password="StrongPass123")

        self.client.login(username="admin2", password="StrongPass123")
        response = self.client.post(
            reverse("frontend:admin_panel"),
            {
                "username": "target1",
                "amount": "250.00",
            },
        )

        self.assertEqual(response.status_code, 302)

        target_profile = UserProfile.objects.get(user=target)
        self.assertEqual(target_profile.balance_coins, Decimal("250.00"))
        self.assertTrue(
            target.ledger_entries.filter(reason=LedgerReason.ADMIN_ADJUST).exists()
        )

    def test_dashboard_shows_online_stats_and_clickable_links(self):
        owner = User.objects.create_user(username="owner", password="StrongPass123")
        owner_profile = UserProfile.objects.get(user=owner)
        owner_profile.role = UserRole.ADMIN
        owner_profile.save(update_fields=["role"])
        target = User.objects.create_user(username="target", password="StrongPass123")
        target_profile = UserProfile.objects.get(user=target)
        target_profile.role = UserRole.MOD
        target_profile.last_seen_at = timezone.now()
        target_profile.save(update_fields=["role", "last_seen_at"])

        item = SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            item_type=SteamItem.ItemType.CASE,
        )
        LedgerEntry.objects.create(
            user=target,
            delta=Decimal("10.00"),
            balance_after=Decimal("10.00"),
            reason=LedgerReason.TOPUP,
        )

        self.client.login(username="owner", password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Модер")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertContains(response, reverse("steam_catalog:detail", args=[item.id]))
        self.assertContains(response, f'{reverse("profiles:profile_detail", args=[target.id])}#recent-operations')

    def test_dashboard_hides_profile_detail_links_for_regular_user(self):
        owner = User.objects.create_user(username="owner_regular", password="StrongPass123")
        target = User.objects.create_user(username="target_regular", password="StrongPass123")
        target_profile = UserProfile.objects.get(user=target)
        target_profile.role = UserRole.USER
        target_profile.last_seen_at = timezone.now()
        target_profile.save(update_fields=["role", "last_seen_at"])

        LedgerEntry.objects.create(
            user=target,
            delta=Decimal("5.00"),
            balance_after=Decimal("5.00"),
            reason=LedgerReason.TOPUP,
        )

        self.client.login(username="owner_regular", password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("profiles:profile_detail", args=[target.id]))

    def test_dashboard_hides_disabled_module_quick_blocks(self):
        owner = User.objects.create_user(username="owner_disabled_blocks", password="StrongPass123")
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
        )

        update_module_states({"farm": False, "steam_catalog": False})

        self.client.login(username="owner_disabled_blocks", password="StrongPass123")
        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("farm:dashboard"))
        self.assertNotContains(response, reverse("steam_catalog:detail", args=[item.id]))
        self.assertNotContains(response, "Последние предметы")

    def test_unknown_url_renders_custom_404_for_anonymous_user(self):
        response = self.client.get("/pro")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Упс, страница не найдена", status_code=404)
        self.assertContains(response, reverse("frontend:home"), status_code=404)
        self.assertContains(response, reverse("accounts:login"), status_code=404)
        self.assertNotContains(response, "Request Method", status_code=404)
        self.assertNotContains(response, "Запрошенный адрес", status_code=404)

    def test_unknown_url_renders_custom_404_for_authenticated_user(self):
        user = User.objects.create_user(username="missing_url_user", password="StrongPass123")
        self.client.login(username="missing_url_user", password="StrongPass123")

        response = self.client.get("/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Упс, страница не найдена", status_code=404)
        self.assertContains(response, reverse("frontend:dashboard_preview"), status_code=404)
        self.assertContains(response, reverse("frontend:home"), status_code=404)
        self.assertNotContains(response, "Request Method", status_code=404)
        self.assertNotContains(response, "Запрошенный адрес", status_code=404)

    def test_unknown_url_renders_custom_not_found_page(self):
        response = self.client.get("/pro")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Упс, страница не найдена", status_code=404)
        self.assertNotContains(response, "Request Method", status_code=404)
        self.assertNotContains(response, "Запрошенный адрес", status_code=404)

    def test_profile_detail_missing_user_renders_custom_not_found_page(self):
        user = User.objects.create_user(username="missingprofile", password="StrongPass123")
        self.client.login(username="missingprofile", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile_detail", args=[999999]))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Страница не найдена", status_code=404)
        self.assertContains(response, "Упс, страница не найдена", status_code=404)
        self.assertNotContains(response, "No UserProfile matches the given query", status_code=404)
        self.assertNotContains(response, "Запрошенный адрес", status_code=404)

    def test_midnight_home_renders_theme_specific_content(self):
        self._activate_midnight_theme()

        response = self.client.get(reverse("frontend:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "midnight-home")
        self.assertContains(response, reverse("frontend:dashboard_preview"))
        self.assertContains(response, reverse("frontend:testing_preview"))

    def test_midnight_public_navbar_uses_named_routes(self):
        self._activate_midnight_theme()

        response = self.client.get(reverse("frontend:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("frontend:home"))
        self.assertContains(response, reverse("frontend:dashboard_preview"))
        self.assertContains(response, reverse("frontend:testing_preview"))
        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, "lang-switcher")

    def test_midnight_dashboard_preview_keeps_topnav_shell(self):
        self._activate_midnight_theme()
        user = User.objects.create_user(username="midnight_dashboard_user", password="StrongPass123")
        self.client.login(username="midnight_dashboard_user", password="StrongPass123")

        response = self.client.get(reverse("frontend:dashboard_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "midnight-topnav")
        self.assertContains(response, "dashboard-shell--midnight")

    def test_runtime_theme_css_is_loaded_after_steam_catalog_css(self):
        user = User.objects.create_user(username="steam_theme_order_user", password="StrongPass123")
        item = SteamItem.objects.create(
            market_hash_name="Order Test Case",
            family_name="Order Test Case",
            name="Order Test Case",
            item_type=SteamItem.ItemType.CASE,
        )
        listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")
        UserThemePurchase.objects.create(
            user=user,
            listing=listing,
            theme_key=listing.theme_key,
            amount_rub=listing.price_rub,
        )

        profile = UserProfile.objects.get(user=user)
        profile.theme_preferences = {"steam_catalog": listing.theme_key}
        profile.save(update_fields=["theme_preferences", "updated_at"])

        update_module_states({"steam_catalog": True, "subscriptions": False, "store": True})

        self.client.login(username="steam_theme_order_user", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        steam_catalog_css_pos = content.rfind("css/modules/steam_catalog.css")
        runtime_theme_css_pos = content.find("steam_catalog_price_graph_plus/css/steam_catalog_price_graph_plus.css")

        self.assertGreaterEqual(steam_catalog_css_pos, 0)
        self.assertGreater(runtime_theme_css_pos, steam_catalog_css_pos)
