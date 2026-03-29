from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem

User = get_user_model()


class FrontendDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_available_for_authenticated_user(self):
        user = User.objects.create_user(username="user1", password="StrongPass123")
        self.client.login(username="user1", password="StrongPass123")

        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CaseUP")

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
        from modules.v2.system.services import update_module_states

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