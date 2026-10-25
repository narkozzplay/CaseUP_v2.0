from __future__ import annotations

import logging
import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.services import health
from modules.v2.notifications.models import Notification

from . import alerts, errors, watchdog
from .models import ErrorGroup

User = get_user_model()


def _boom(message="broken"):
    raise ValueError(message)


def _exc_info(message="broken"):
    try:
        _boom(message)
    except ValueError:
        return sys.exc_info()


def _make_admin(username="ops_admin"):
    from modules.v2.profiles.models import UserProfile, UserRole

    admin = User.objects.create_user(username=username, password="x")
    UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
    return admin


class ErrorJournalTests(TestCase):
    def test_same_error_is_one_group_with_a_counter(self):
        first = errors.record_error(source="task", exc_info=_exc_info("item 1"), context="celery_tasks.run")
        second = errors.record_error(source="task", exc_info=_exc_info("item 2"), context="celery_tasks.run")
        self.assertEqual(first.pk, second.pk)
        group = ErrorGroup.objects.get()
        self.assertEqual(group.count, 2)
        self.assertEqual(group.title, "ValueError: item 2")
        self.assertIn("test_monitoring.py:_boom", group.location)
        self.assertIn("Traceback", group.last_traceback)

    def test_resolved_error_reopens_and_is_announced_again(self):
        group = errors.record_error(source="web", exc_info=_exc_info())
        ErrorGroup.objects.filter(pk=group.pk).update(resolved_at=timezone.now(), alerted_at=timezone.now())
        errors.record_error(source="web", exc_info=_exc_info())
        group.refresh_from_db()
        self.assertIsNone(group.resolved_at)
        self.assertIsNone(group.alerted_at)

    def test_logging_handler_records_request_errors_and_skips_noise(self):
        handler = errors.ErrorJournalHandler()
        request = MagicMock(method="GET", path="/market/")
        record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: %s", ("/market/",), _exc_info())
        record.request = request
        handler.emit(record)
        noise = logging.LogRecord("django.security.DisallowedHost", logging.ERROR, __file__, 1, "Invalid HTTP_HOST", (), None)
        handler.emit(noise)
        plain = logging.LogRecord("modules.v2.farm", logging.ERROR, __file__, 1, "sync failed for %s", ("acc 17",), None)
        handler.emit(plain)
        handler.emit(logging.LogRecord("modules.v2.farm", logging.ERROR, __file__, 1, "sync failed for %s", ("acc 18",), None))

        web = ErrorGroup.objects.get(source="web")
        self.assertEqual(web.last_context, "GET /market/")
        other = ErrorGroup.objects.get(source="other")
        self.assertEqual(other.count, 2)  # one template, two accounts
        self.assertEqual(ErrorGroup.objects.count(), 2)

    def test_celery_task_failure(self):
        exc_type, exc, tb = _exc_info("no price")
        task = MagicMock()
        task.name = "celery_tasks.run_due_price_auto_updates"
        errors.record_task_failure(sender=task, exception=exc, traceback=tb)
        group = ErrorGroup.objects.get()
        self.assertEqual(group.source, "task")
        self.assertEqual(group.last_context, "celery_tasks.run_due_price_auto_updates")


class AlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _make_admin()

    @override_settings(ADMINS=[("Ops", "ops@example.com")])
    def test_alert_goes_to_admins_and_falls_back_to_mail_without_telegram(self):
        with patch.object(alerts, "_send_telegram", return_value=False):
            result = alerts.send_admin_alert("db", "База данных: проблема", "Нет соединения")
        self.assertEqual(result, {"telegram": False, "notified": 1, "mail": True})
        note = Notification.objects.get(recipient=self.admin, kind=alerts.ALERT_KIND)
        self.assertIn("База данных", note.payload["title"])
        self.assertEqual(len(mail.outbox), 1)

    def test_repeat_is_skipped_and_telegram_replaces_mail(self):
        with patch.object(alerts, "_send_telegram", return_value=True) as telegram:
            self.assertIsNotNone(alerts.send_admin_alert("disk", "Диск", "90%"))
            self.assertIsNone(alerts.send_admin_alert("disk", "Диск", "91%"))
        telegram.assert_called_once()
        self.assertIn("🚨 CaseUP: Диск", telegram.call_args.args[0])
        self.assertEqual(len(mail.outbox), 0)


class WatchdogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _make_admin()

    def _checks(self, status):
        return [health.HealthCheck("database", "База данных", status, "Нет соединения" if status == health.FAIL else "3 мс")]

    def test_alert_on_break_silence_while_broken_and_recovery(self):
        sent = []
        with patch.object(watchdog, "send_admin_alert", side_effect=lambda *a, **k: sent.append((a, k)) or {}):
            with patch.object(watchdog, "_health_checks", return_value=self._checks(health.FAIL)):
                self.assertEqual(watchdog.check_health(), {"database": "alert"})
            with patch.object(watchdog, "_health_checks", return_value=self._checks(health.OK)):
                self.assertEqual(watchdog.check_health(), {"database": "recovered"})
                self.assertEqual(watchdog.check_health(), {})
        self.assertEqual(len(sent), 2)
        self.assertTrue(sent[1][1]["ok"])

    def test_still_broken_is_reminded_only_after_the_pause(self):
        with patch.object(alerts, "_send_telegram", return_value=True) as telegram, patch.object(watchdog, "_health_checks", return_value=self._checks(health.FAIL)):
            watchdog.check_health()
            watchdog.check_health()
        self.assertEqual(telegram.call_count, 1)

    def test_new_errors_are_announced_once(self):
        errors.record_error(source="web", exc_info=_exc_info())
        with patch.object(alerts, "_send_telegram", return_value=True) as telegram:
            self.assertEqual(watchdog.check_new_errors(), 1)
            self.assertEqual(watchdog.check_new_errors(), 0)
        self.assertIn("Новая ошибка", telegram.call_args.args[0])
        self.assertIsNotNone(ErrorGroup.objects.get().alerted_at)

    def test_stopped_telegram_poller_raises_an_alert(self):
        from modules.v2.tg_notifier.models import TelegramNotifierConfig

        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEFabcdefABCDEFabcdefABCDEFabc")
        with patch.object(watchdog, "send_admin_alert", return_value={}) as alert:
            self.assertEqual(watchdog.check_telegram_poller(), "alert")
        self.assertIn("telegram_poller", alert.call_args.args[2])

    def _poll_status(self, *, error="", failing_minutes=0, ok_minutes=0, age_minutes=0):
        from modules.v2.tg_notifier.updates import POLL_STATUS_KEY

        now = timezone.now()
        cache.set(POLL_STATUS_KEY, {
            "at": now - timedelta(minutes=age_minutes),
            "error": error,
            "handled": 0,
            "failing_since": now - timedelta(minutes=failing_minutes) if error else None,
            "ok_since": None if error else now - timedelta(minutes=ok_minutes),
        })

    def test_telegram_poller_hiccups_do_not_flood_admins(self):
        from modules.v2.tg_notifier.models import TelegramNotifierConfig

        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEFabcdefABCDEFabcdefABCDEFabc")
        with patch.object(watchdog, "send_admin_alert", return_value={}) as alert:
            # A single 502 and the recovery after it: nothing to report.
            self._poll_status(error="Telegram API HTTP 502", failing_minutes=1)
            self.assertEqual(watchdog.check_telegram_poller(), "")
            self._poll_status(ok_minutes=2)
            self.assertEqual(watchdog.check_telegram_poller(), "")
            alert.assert_not_called()

            # Sustained failure: one alert.
            self._poll_status(error="Telegram API HTTP 502", failing_minutes=20)
            self.assertEqual(watchdog.check_telegram_poller(), "alert")
            self.assertIn("502", alert.call_args.args[2])

            # A poll got through between errors: still unstable, no «снова в норме» yet.
            self._poll_status(ok_minutes=3)
            self.assertEqual(watchdog.check_telegram_poller(), "")
            self.assertEqual(alert.call_count, 1)

            # Clean long enough: one recovery message.
            self._poll_status(ok_minutes=30)
            self.assertEqual(watchdog.check_telegram_poller(), "recovered")
            self.assertEqual(alert.call_count, 2)

            # Stopped poller: alert; restarted, the first clean poll is not a recovery yet.
            self._poll_status(ok_minutes=40, age_minutes=10)
            self.assertEqual(watchdog.check_telegram_poller(), "alert")
            self._poll_status(ok_minutes=1)
            self.assertEqual(watchdog.check_telegram_poller(), "")
            self.assertEqual(alert.call_count, 3)

    def test_run_is_the_heartbeat(self):
        with patch.object(watchdog, "_health_checks", return_value=[]):
            watchdog.run_watchdog()
        self.assertLess(watchdog.heartbeat_age_seconds(), 5)

    def test_real_checks_run_without_crashing(self):
        with patch.object(alerts, "_send_telegram", return_value=True):
            report = watchdog.run_watchdog()
        self.assertNotIn("error", report.values())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_scheduler_check(self):
        self.assertEqual(health.check_scheduler().status, health.FAIL)
        watchdog.beat()
        self.assertEqual(health.check_scheduler().status, health.OK)
        cache.set(watchdog.HEARTBEAT_KEY, timezone.now() - timedelta(minutes=30))
        self.assertEqual(health.check_scheduler().status, health.FAIL)


class ReadinessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_ready_when_everything_works(self):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_503_when_background_tasks_stopped(self):
        with patch.object(health, "check_scheduler", return_value=health.HealthCheck("scheduler", "Планировщик задач", health.FAIL, "Нет пульса")):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["scheduler"], "fail")


class ErrorsPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _make_admin()
        self.client.force_login(self.admin)

    def test_page_lists_and_resolves_errors(self):
        group = errors.record_error(source="task", exc_info=_exc_info("Steam 429"), context="celery_tasks.run")
        page = self.client.get(reverse("control_center:errors"))
        self.assertContains(page, "ValueError: Steam 429")
        self.assertContains(page, "celery_tasks.run")

        self.client.post(reverse("control_center:errors_action"), {"action": "resolve", "group_id": group.pk})
        group.refresh_from_db()
        self.assertIsNotNone(group.resolved_at)
        self.assertContains(self.client.get(reverse("control_center:errors")), "Открытых ошибок нет")

    def test_test_alert_button(self):
        with patch.object(alerts, "_send_telegram", return_value=True):
            response = self.client.post(reverse("control_center:errors_action"), {"action": "test_alert"}, follow=True)
        self.assertContains(response, "Тревога отправлена: Telegram")

    def test_regular_user_has_no_access(self):
        self.client.force_login(User.objects.create_user(username="plain", password="x"))
        self.assertEqual(self.client.get(reverse("control_center:errors")).status_code, 403)
