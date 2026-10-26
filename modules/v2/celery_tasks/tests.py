from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserRole
from modules.v2.steam_catalog.models import SteamItem

from .models import PriceAutoUpdateRule, PriceAutoUpdateRunLog
from .services import run_due_rules, run_price_auto_update_rule
from .tasks import dispatch_pending_price_drop_alerts_task, dispatch_due_steam_trade_unlocks_task

User = get_user_model()


class CeleryTasksTests(TestCase):
    def setUp(self):
        # Migrations create the default queue rules; these tests build their own.
        PriceAutoUpdateRule.objects.all().delete()

    def _make_admin_user(self, username="admin_user"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        user.profile_v2.role = UserRole.ADMIN
        user.profile_v2.save(update_fields=["role"])
        return user

    def _make_regular_user(self, username="regular_user"):
        return User.objects.create_user(username=username, password="StrongPass123")

    PRICE_FETCH = "modules.v2.steam_catalog.services.fetch_price_rub_from_steam"

    def _item(self, name, category=SteamItem.ItemType.CASE, price="100.00", **extra):
        return SteamItem.objects.create(market_hash_name=name, family_name=extra.pop("family", name), name=name, item_type=category, price_rub=Decimal(price), **extra)

    @patch(PRICE_FETCH)
    def test_run_takes_a_queue_batch_and_logs_it(self, fetch_mock):
        rule = PriceAutoUpdateRule.objects.create(category=SteamItem.ItemType.SKIN, interval_minutes=30, is_enabled=True, next_run_at=timezone.now())
        self._item("AK-47 | Redline (Factory New)", SteamItem.ItemType.SKIN, family="AK-47 | Redline")
        self._item("AK-47 | Redline (Field-Tested)", SteamItem.ItemType.SKIN, "110.00", family="AK-47 | Redline")
        self._item("M4A4 | Asiimov (Battle-Scarred)", SteamItem.ItemType.SKIN, "200.00", family="M4A4 | Asiimov")
        fetch_mock.side_effect = [Decimal("120.00"), Decimal("110.00"), Decimal("250.00")]

        summary = run_price_auto_update_rule(rule.id, triggered_by="manual")
        rule.refresh_from_db()
        run_log = PriceAutoUpdateRunLog.objects.get(rule=rule)

        self.assertEqual(fetch_mock.call_count, 3)
        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual((summary.touched_items_count, summary.updated_items_count, summary.history_written_count), (3, 2, 3))
        self.assertEqual(rule.last_status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertIsNotNone(rule.next_run_at)
        self.assertEqual((run_log.triggered_by, run_log.status, run_log.errors_count), ("manual", PriceAutoUpdateRule.Status.SUCCESS, 0))
        self.assertFalse(SteamItem.objects.filter(price_checked_at__isnull=True).exists())

        # Freshly checked items are not asked again on the next run.
        summary = run_price_auto_update_rule(rule.id, triggered_by="manual")
        self.assertEqual((fetch_mock.call_count, summary.touched_items_count), (3, 0))

    @patch(PRICE_FETCH)
    def test_run_writes_partial_log_when_one_item_fails(self, fetch_mock):
        rule = PriceAutoUpdateRule.objects.create(category=SteamItem.ItemType.CASE, interval_minutes=10, is_enabled=True, next_run_at=timezone.now())
        self._item("Recoil Case")
        self._item("Kilowatt Case", price="200.00")
        fetch_mock.side_effect = [Decimal("101.00"), RuntimeError("steam timeout")]

        summary = run_price_auto_update_rule(rule.id, triggered_by="beat")
        run_log = PriceAutoUpdateRunLog.objects.get(rule=rule)

        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.PARTIAL)
        self.assertEqual((summary.touched_items_count, summary.updated_items_count, summary.errors_count), (1, 1, 1))
        self.assertEqual(run_log.status, PriceAutoUpdateRule.Status.PARTIAL)
        self.assertIn("Kilowatt Case", run_log.error_summary)
        self.assertIn("steam timeout", run_log.error_summary)

    @patch(PRICE_FETCH)
    def test_run_recovers_stale_running_state(self, fetch_mock):
        rule = PriceAutoUpdateRule.objects.create(
            category=SteamItem.ItemType.CASE, interval_minutes=10, is_enabled=True, next_run_at=timezone.now(), last_status=PriceAutoUpdateRule.Status.RUNNING
        )
        self._item("Recoil Case")
        PriceAutoUpdateRule.objects.filter(pk=rule.pk).update(updated_at=timezone.now() - timedelta(hours=2))
        fetch_mock.return_value = Decimal("105.00")

        summary = run_price_auto_update_rule(rule.id, triggered_by="beat")

        rule.refresh_from_db()
        self.assertEqual(summary.status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(rule.last_status, PriceAutoUpdateRule.Status.SUCCESS)
        self.assertEqual(PriceAutoUpdateRunLog.objects.filter(rule=rule).count(), 2)
        self.assertTrue(PriceAutoUpdateRunLog.objects.filter(rule=rule, status=PriceAutoUpdateRule.Status.ERROR, error_summary__icontains="Recovered stale RUNNING state").exists())

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
        run_rule_mock.assert_called_once()
        self.assertEqual(run_rule_mock.call_args.args, (due_rule.id,))
        self.assertEqual(run_rule_mock.call_args.kwargs["triggered_by"], "beat")
        self.assertIsNotNone(run_rule_mock.call_args.kwargs["deadline"])

    @patch("modules.v2.celery_tasks.services.run_price_auto_update_rule")
    def test_run_due_rules_shares_time_and_survives_a_failing_rule(self, run_rule_mock):
        now = timezone.now()
        for category in (SteamItem.ItemType.CASE, SteamItem.ItemType.SKIN, SteamItem.ItemType.STICKER):
            PriceAutoUpdateRule.objects.create(category=category, interval_minutes=60, is_enabled=True, next_run_at=now - timedelta(minutes=1))
        deadlines = []

        def run(rule_id, *, triggered_by, deadline):
            deadlines.append(deadline)
            if len(deadlines) == 1:
                raise RuntimeError("steam down")
            return rule_id

        run_rule_mock.side_effect = run
        start = time.monotonic()
        results = run_due_rules(triggered_by="beat", deadline=start + 900)

        self.assertEqual(len(results), 2)
        self.assertEqual(run_rule_mock.call_count, 3)
        # The first rule gets about a third of the time, not all of it.
        self.assertLess(deadlines[0] - start, 310)
        self.assertTrue(all(deadline <= start + 900 for deadline in deadlines))

    @patch("modules.v2.celery_tasks.services.refresh_batch")
    def test_interrupted_rule_keeps_a_readable_error(self, refresh_mock):
        from celery.exceptions import SoftTimeLimitExceeded

        rule = PriceAutoUpdateRule.objects.create(category=SteamItem.ItemType.SKIN, interval_minutes=60, is_enabled=True)
        refresh_mock.side_effect = SoftTimeLimitExceeded()
        with self.assertRaises(SoftTimeLimitExceeded):
            run_price_auto_update_rule(rule.id, triggered_by="beat")
        rule.refresh_from_db()
        self.assertEqual(rule.last_status, PriceAutoUpdateRule.Status.ERROR)
        self.assertTrue(rule.last_error)

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
        self.assertContains(response, "Автообновление цен")
        self.assertContains(response, "История запусков")

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
        self.assertContains(response, "по расписанию")
        self.assertContains(response, "Успешно")
        self.assertContains(response, "из 7")

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

class AutoPriceFormTests(TestCase):
    def setUp(self):
        PriceAutoUpdateRule.objects.all().delete()

    def test_category_choices_skip_categories_that_already_have_a_rule(self):
        from .forms import PriceAutoUpdateRuleForm

        rule = PriceAutoUpdateRule.objects.create(category=SteamItem.ItemType.CASE, interval_minutes=60)
        codes = [code for code, _label in PriceAutoUpdateRuleForm().fields["category"].choices]
        self.assertNotIn(SteamItem.ItemType.CASE, codes)
        self.assertIn(SteamItem.ItemType.SKIN, codes)
        # Editing a rule keeps its own category selectable.
        own = [code for code, _label in PriceAutoUpdateRuleForm(instance=rule).fields["category"].choices]
        self.assertIn(SteamItem.ItemType.CASE, own)
        labels = dict(PriceAutoUpdateRuleForm().fields["category"].choices)
        self.assertEqual(labels[SteamItem.ItemType.SKIN], "Скины")


class PriceSchedulerPaceTests(TestCase):
    def test_every_category_has_a_rule(self):
        self.assertEqual(
            set(PriceAutoUpdateRule.objects.values_list("category", flat=True)),
            {code for code, _label in SteamItem.ItemType.choices},
        )

    @override_settings(CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS=300, CELERY_TASK_SOFT_TIME_LIMIT=900)
    @patch("modules.v2.celery_tasks.services.run_price_auto_update_rule")
    def test_beat_run_ends_before_the_next_one(self, run_rule_mock):
        PriceAutoUpdateRule.objects.update(next_run_at=None, is_enabled=False)
        PriceAutoUpdateRule.objects.filter(category=SteamItem.ItemType.SKIN).update(is_enabled=True, next_run_at=timezone.now())
        start = time.monotonic()
        run_due_rules(triggered_by="beat")
        self.assertLessEqual(run_rule_mock.call_args.kwargs["deadline"] - start, 300)

    @patch("modules.v2.celery_tasks.tasks.run_due_rules", return_value=[])
    def test_a_busy_run_is_not_joined_by_a_second_one(self, run_mock):
        from django.core.cache import cache

        from .tasks import PRICE_RUN_LOCK_KEY, run_due_price_auto_updates_task

        cache.set(PRICE_RUN_LOCK_KEY, "other-run", 60)
        self.assertEqual(run_due_price_auto_updates_task(), [])
        run_mock.assert_not_called()
        self.assertEqual(cache.get(PRICE_RUN_LOCK_KEY), "other-run")  # someone else's lock is left alone
        cache.delete(PRICE_RUN_LOCK_KEY)
        run_due_price_auto_updates_task()
        run_mock.assert_called_once()
        self.assertIsNone(cache.get(PRICE_RUN_LOCK_KEY))

    def test_a_rule_due_just_after_the_tick_runs_on_it(self):
        from .services import get_due_rules

        PriceAutoUpdateRule.objects.update(is_enabled=True, next_run_at=timezone.now() + timedelta(hours=1))
        PriceAutoUpdateRule.objects.filter(category=SteamItem.ItemType.SKIN).update(next_run_at=timezone.now() + timedelta(seconds=20))
        self.assertEqual(list(get_due_rules().values_list("category", flat=True)), [SteamItem.ItemType.SKIN])
