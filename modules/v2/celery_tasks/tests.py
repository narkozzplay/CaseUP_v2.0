from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserRole
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.steam_catalog.services import RefreshPriceResult

from .models import PriceAutoUpdateRule, PriceAutoUpdateRunLog
from .services import run_due_rules, run_price_auto_update_rule
from .tasks import dispatch_pending_price_drop_alerts_task, dispatch_due_steam_trade_unlocks_task

User = get_user_model()


class CeleryTasksTests(TestCase):
    def _make_admin_user(self, username="admin_user"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        user.profile_v2.role = UserRole.ADMIN
        user.profile_v2.save(update_fields=["role"])
        return user

    def _make_regular_user(self, username="regular_user"):
        return User.objects.create_user(username=username, password="StrongPass123")

    @patch("modules.v2.celery_tasks.services.refresh_item_prices")
    def test_run_price_auto_update_rule_for_skin_runs_once_per_family(self, refresh_mock):
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.SKIN,
            interval_minutes=30,
            is_enabled=True,
            next_run_at=timezone.now(),
        )

        SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Factory New)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline (Factory New)",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("100.00"),
        )
        SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline (Field-Tested)",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("110.00"),
        )
        SteamItem.objects.create(
            market_hash_name="M4A4 | Asiimov (Battle-Scarred)",
            family_name="M4A4 | Asiimov",
            name="M4A4 | Asiimov (Battle-Scarred)",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("200.00"),
        )

        refresh_mock.side_effect = [
            RefreshPriceResult(updated_count=2, history_written=2, touched_count=2, last_synced_at=timezone.now()),
            RefreshPriceResult(updated_count=1, history_written=1, touched_count=1, last_synced_at=timezone.now()),
        ]

        summary = run_price_auto_update_rule(rule.id, triggered_by="manual")
        rule.refresh_from_db()
        run_log = PriceAutoUpdateRunLog.objects.get(rule=rule)

        self.assertEqual(refresh_mock.call_count, 2)
        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(summary.updated_items_count, 3)
        self.assertEqual(summary.history_written_count, 3)
        self.assertEqual(summary.touched_items_count, 3)
        self.assertEqual(rule.last_status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertIsNotNone(rule.last_run_at)
        self.assertIsNotNone(rule.next_run_at)

        self.assertEqual(run_log.triggered_by, "manual")
        self.assertEqual(run_log.status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(run_log.touched_items_count, 3)
        self.assertEqual(run_log.updated_items_count, 3)
        self.assertEqual(run_log.history_written_count, 3)
        self.assertEqual(run_log.errors_count, 0)
        self.assertIsNotNone(run_log.finished_at)

        for mock_call in refresh_mock.call_args_list:
            self.assertTrue(mock_call.kwargs["include_family"])

    @patch("modules.v2.celery_tasks.services.refresh_item_prices")
    def test_run_price_auto_update_rule_writes_partial_log_when_one_item_fails(self, refresh_mock):
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=10,
            is_enabled=True,
            next_run_at=timezone.now(),
        )

        first = SteamItem.objects.create(
            market_hash_name="Recoil Case",
            family_name="Recoil Case",
            name="Recoil Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("100.00"),
        )
        second = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("200.00"),
        )

        refresh_mock.side_effect = [
            RefreshPriceResult(updated_count=1, history_written=1, touched_count=1, last_synced_at=timezone.now()),
            RuntimeError("steam timeout"),
        ]

        summary = run_price_auto_update_rule(rule.id, triggered_by="beat")
        run_log = PriceAutoUpdateRunLog.objects.get(rule=rule)

        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.PARTIAL)
        self.assertEqual(summary.touched_items_count, 1)
        self.assertEqual(summary.updated_items_count, 1)
        self.assertEqual(summary.history_written_count, 1)
        self.assertEqual(summary.errors_count, 1)

        self.assertEqual(run_log.triggered_by, "beat")
        self.assertEqual(run_log.status, PriceAutoUpdateRule.Status.PARTIAL)
        self.assertEqual(run_log.touched_items_count, 1)
        self.assertEqual(run_log.updated_items_count, 1)
        self.assertEqual(run_log.history_written_count, 1)
        self.assertEqual(run_log.errors_count, 1)
        self.assertIn(first.market_hash_name, run_log.error_summary)
        self.assertIn("steam timeout", run_log.error_summary)

        self.assertEqual(refresh_mock.call_count, 2)


    @patch("modules.v2.celery_tasks.services.refresh_item_prices")
    def test_run_price_auto_update_rule_recovers_stale_running_state(self, refresh_mock):
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=10,
            is_enabled=True,
            next_run_at=timezone.now(),
            last_status=PriceAutoUpdateRule.Status.RUNNING,
        )
        SteamItem.objects.create(
            market_hash_name="Recoil Case",
            family_name="Recoil Case",
            name="Recoil Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("100.00"),
        )
        PriceAutoUpdateRule.objects.filter(pk=rule.pk).update(updated_at=timezone.now() - timedelta(hours=2))

        refresh_mock.return_value = RefreshPriceResult(
            updated_count=1,
            history_written=1,
            touched_count=1,
            last_synced_at=timezone.now(),
        )

        summary = run_price_auto_update_rule(rule.id, triggered_by="beat")

        rule.refresh_from_db()
        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(rule.last_status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(PriceAutoUpdateRunLog.objects.filter(rule=rule).count(), 2)
        self.assertTrue(
            PriceAutoUpdateRunLog.objects.filter(
                rule=rule,
                status=PriceAutoUpdateRule.Status.ERROR,
                error_summary__icontains="Recovered stale RUNNING state",
            ).exists()
        )

    @patch("modules.v2.celery_tasks.services.run_price_auto_update_rule")
    def test_run_due_rules_executes_only_due_enabled_rules(self, run_rule_mock):
        now = timezone.now()

        due_rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=60,
            is_enabled=True,
            next_run_at=now - timedelta(minutes=1),
        )
        PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.STICKER,
            interval_minutes=60,
            is_enabled=True,
            next_run_at=now + timedelta(minutes=20),
        )
        PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.OTHER,
            interval_minutes=60,
            is_enabled=False,
            next_run_at=now - timedelta(minutes=1),
        )

        run_rule_mock.return_value = type(
            "Summary",
            (),
            {
                "rule_id": due_rule.id,
                "category": due_rule.category,
                "status": PriceAutoUpdateRule.Status.SUCCESS,
                "touched_items_count": 1,
                "updated_items_count": 1,
                "history_written_count": 1,
                "errors_count": 0,
            },
        )()

        results = run_due_rules(triggered_by="beat")

        self.assertEqual(len(results), 1)
        run_rule_mock.assert_called_once_with(due_rule.id, triggered_by="beat")

    def test_control_center_forbidden_for_regular_user(self):
        user = self._make_regular_user()
        self.client.force_login(user)

        response = self.client.get(reverse("celery_tasks:control_center"))
        self.assertEqual(response.status_code, 403)

    def test_control_center_available_for_admin_plus(self):
        user = self._make_admin_user()
        self.client.force_login(user)

        response = self.client.get(reverse("celery_tasks:control_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Celery Tasks")
        self.assertContains(response, "Run logs")

    def test_control_center_can_create_rule(self):
        user = self._make_admin_user()
        self.client.force_login(user)
    
        response = self.client.post(
            reverse("celery_tasks:control_center"),
            {
                "category": SteamItem.ItemType.CASE,
                "schedule_mode": PriceAutoUpdateRule.ScheduleMode.INTERVAL,
                "interval_minutes": 60,
                "is_enabled": "on",
            },
        )
    
        self.assertEqual(response.status_code, 302)
        rule = PriceAutoUpdateRule.objects.get(category=SteamItem.ItemType.CASE)
        self.assertTrue(rule.is_enabled)
        self.assertEqual(rule.interval_minutes, 60)
        self.assertEqual(rule.schedule_mode, PriceAutoUpdateRule.ScheduleMode.INTERVAL)
        self.assertIsNotNone(rule.next_run_at)
    
    def test_control_center_shows_recent_logs(self):
        user = self._make_admin_user()
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=60,
            is_enabled=True,
        )
        PriceAutoUpdateRunLog.objects.create(
            rule=rule,
            category=rule.category,
            triggered_by="beat",
            status=PriceAutoUpdateRule.Status.SUCCESS,
            touched_items_count=7,
            updated_items_count=1,
            history_written_count=1,
            errors_count=0,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

        self.client.force_login(user)
        response = self.client.get(reverse("celery_tasks:control_center"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "beat")
        self.assertContains(response, "success")
        self.assertContains(response, "7")

    @patch("modules.v2.celery_tasks.views.run_price_auto_update_rule_task.delay")
    def test_run_now_view_sends_task_to_queue(self, delay_mock):
        user = self._make_admin_user()
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=60,
            is_enabled=True,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("celery_tasks:run_rule_now", args=[rule.id]))

        self.assertEqual(response.status_code, 302)
        delay_mock.assert_called_once_with(rule.id, triggered_by="manual")

    def test_toggle_rule_switches_enabled_state(self):
        user = self._make_admin_user()
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE,
            interval_minutes=60,
            is_enabled=True,
            next_run_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.post(reverse("celery_tasks:toggle_rule", args=[rule.id]))
        self.assertEqual(response.status_code, 302)

        rule.refresh_from_db()
        self.assertFalse(rule.is_enabled)
        self.assertIsNone(rule.next_run_at)


    @patch("modules.v2.celery_tasks.tasks.dispatch_due_unlock_notifications")
    def test_dispatch_due_steam_trade_unlocks_task_returns_summary(self, dispatch_mock):
        dispatch_mock.return_value = type("Summary", (), {
            "processed_count": 4,
            "sent_count": 3,
            "error_count": 1,
        })()

        result = dispatch_due_steam_trade_unlocks_task(limit=25)

        self.assertEqual(result["processed_count"], 4)
        self.assertEqual(result["sent_count"], 3)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["limit"], 25)
        dispatch_mock.assert_called_once_with(limit=25)

    @patch("modules.v2.celery_tasks.tasks.dispatch_pending_price_drop_alerts")
    def test_dispatch_pending_price_drop_alerts_task_returns_sent_count(self, dispatch_mock):
        dispatch_mock.return_value = 3
    
        result = dispatch_pending_price_drop_alerts_task(limit=15)
    
        self.assertEqual(result["sent_count"], 3)
        self.assertEqual(result["limit"], 15)
        dispatch_mock.assert_called_once_with(limit=15)