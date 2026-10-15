from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile, UserRole

from . import geoip
from .devices import parse_user_agent
from .models import UserSession
from .notifications import KIND_NEW_LOGIN
from .sessions import end_all_sessions, prune_sessions

User = get_user_model()
PASSWORD = "StrongPass123"
CHROME_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"


class UserAgentParserTests(TestCase):
    def test_common_devices(self):
        cases = {
            CHROME_WINDOWS: ("desktop", "Windows", "Chrome 126"),
            SAFARI_IPHONE: ("mobile", "iOS 17", "Safari 17"),
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36": ("mobile", "Android 14", "Chrome 125"),
            "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1": ("tablet", "iPadOS", "Safari 17"),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 YaBrowser/24.6.0.0 Safari/537.36": ("desktop", "Windows", "Яндекс Браузер 24"),
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0": ("desktop", "Linux", "Firefox 128"),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0": ("desktop", "Windows", "Edge 126"),
            "curl/8.5.0": ("bot", "", ""),
        }
        for ua, expected in cases.items():
            with self.subTest(ua=ua[:40]):
                info = parse_user_agent(ua)
                self.assertEqual((info.device_type, info.os, info.browser), expected)
        self.assertEqual(parse_user_agent("").label, "Неизвестное устройство")


class GeoIpTests(TestCase):
    def test_lookup_uses_russian_names_and_handles_private_ips(self):
        fake = mock.Mock()
        fake.get.return_value = {"city": {"names": {"en": "Moscow", "ru": "Москва"}}, "country": {"names": {"en": "Russia"}}}
        with mock.patch.object(geoip, "_get_reader", return_value=fake):
            self.assertEqual(geoip.lookup("95.165.1.1"), ("Москва", "Russia"))
            self.assertEqual(geoip.lookup("192.168.1.10"), ("Локальная сеть", ""))
            self.assertEqual(geoip.lookup("not-an-ip"), ("", ""))

    def test_lookup_without_database_is_empty(self):
        with mock.patch.object(geoip, "_get_reader", return_value=None):
            self.assertEqual(geoip.lookup("95.165.1.1"), ("", ""))


class SessionTrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="sess_user", password=PASSWORD, is_staff=True)

    def _login(self, ua=CHROME_WINDOWS):
        client = Client(HTTP_USER_AGENT=ua)
        response = client.post(reverse("admin:login"), {"username": "sess_user", "password": PASSWORD, "next": "/"})
        self.assertEqual(response.status_code, 302)
        return client

    def test_login_records_device_ip_and_method(self):
        client = self._login()
        record = UserSession.objects.get(user=self.user)
        self.assertEqual(record.session_key, client.session.session_key)
        self.assertEqual((record.device_type, record.os, record.browser), ("desktop", "Windows", "Chrome 126"))
        self.assertEqual(record.ip_address, "127.0.0.1")
        self.assertEqual(record.login_method, UserSession.Method.PASSWORD)
        self.assertIsNone(record.ended_at)

    def test_logout_closes_the_record(self):
        client = self._login()
        client.post(reverse("accounts:logout"))
        record = UserSession.objects.get(user=self.user)
        self.assertEqual(record.ended_reason, UserSession.EndReason.LOGOUT)

    def test_security_page_lists_sessions_and_ends_other_ones(self):
        phone = self._login(SAFARI_IPHONE)
        laptop = self._login(CHROME_WINDOWS)

        response = laptop.get(reverse("accounts:security"))
        self.assertContains(response, "iOS 17 · Safari 17")
        self.assertContains(response, "это устройство")
        self.assertEqual(len(response.context["active_sessions"]), 2)

        laptop.post(reverse("accounts:security"), {"action": "end_others"})
        self.assertEqual(phone.get(reverse("accounts:security")).status_code, 302)  # signed out → login redirect
        self.assertEqual(laptop.get(reverse("accounts:security")).status_code, 200)
        ended = UserSession.objects.get(user=self.user, os="iOS 17")
        self.assertEqual(ended.ended_reason, UserSession.EndReason.REVOKED)

    def test_end_single_session_and_not_the_current_one(self):
        phone = self._login(SAFARI_IPHONE)
        laptop = self._login(CHROME_WINDOWS)
        phone_record = UserSession.objects.get(os="iOS 17")
        current = UserSession.objects.get(session_key=laptop.session.session_key)

        laptop.post(reverse("accounts:security"), {"action": "end", "session_id": current.pk})
        self.assertEqual(laptop.get(reverse("accounts:security")).status_code, 200)

        laptop.post(reverse("accounts:security"), {"action": "end", "session_id": phone_record.pk})
        self.assertFalse(Session.objects.filter(session_key=phone_record.session_key).exists())
        self.assertEqual(phone.get(reverse("accounts:security")).status_code, 302)

    def test_cannot_end_someone_elses_session(self):
        other = User.objects.create_user(username="other_sess", password=PASSWORD, is_staff=True)
        other_client = Client()
        other_client.post(reverse("admin:login"), {"username": "other_sess", "password": PASSWORD})
        foreign = UserSession.objects.get(user=other)

        client = self._login()
        client.post(reverse("accounts:security"), {"action": "end", "session_id": foreign.pk})
        self.assertIsNone(UserSession.objects.get(pk=foreign.pk).ended_at)

    def test_new_device_notification(self):
        self._login(CHROME_WINDOWS)  # first ever login: nothing to compare to
        self._login(CHROME_WINDOWS)  # same device again
        self.assertFalse(Notification.objects.filter(recipient=self.user, kind=KIND_NEW_LOGIN).exists())
        self._login(SAFARI_IPHONE)
        notification = Notification.objects.get(recipient=self.user, kind=KIND_NEW_LOGIN)
        self.assertEqual(notification.payload["device"], "iOS 17 · Safari 17")

    def test_session_started_before_tracking_gets_a_record_on_next_request(self):
        client = self._login()
        UserSession.objects.all().delete()
        cache.clear()
        client.get(reverse("accounts:security"))
        self.assertTrue(UserSession.objects.filter(session_key=client.session.session_key).exists())

    def test_activity_is_throttled(self):
        client = self._login()
        record = UserSession.objects.get(user=self.user)
        UserSession.objects.filter(pk=record.pk).update(last_seen_at=timezone.now() - timedelta(hours=1))
        client.get(reverse("accounts:security"))  # within the throttle window right after login
        self.assertLess(UserSession.objects.get(pk=record.pk).last_seen_at, timezone.now() - timedelta(minutes=30))
        cache.clear()
        client.get(reverse("accounts:security"))
        self.assertGreater(UserSession.objects.get(pk=record.pk).last_seen_at, timezone.now() - timedelta(minutes=1))

    def test_end_all_sessions_also_signs_out_untracked_sessions(self):
        tracked = self._login()
        untracked = self._login(SAFARI_IPHONE)
        UserSession.objects.filter(session_key=untracked.session.session_key).delete()

        self.assertEqual(end_all_sessions(self.user), 2)
        self.assertEqual(tracked.get(reverse("accounts:security")).status_code, 302)
        self.assertEqual(untracked.get(reverse("accounts:security")).status_code, 302)

    def test_prune_closes_expired_and_deletes_old_history(self):
        client = self._login()
        Session.objects.filter(session_key=client.session.session_key).update(expire_date=timezone.now() - timedelta(minutes=1))
        old = UserSession.objects.create(user=self.user, session_key="x" * 40, ended_at=timezone.now() - timedelta(days=200))

        result = prune_sessions()

        self.assertEqual(result["expired"], 1)
        self.assertFalse(UserSession.objects.filter(pk=old.pk).exists())
        self.assertEqual(UserSession.objects.get(user=self.user).ended_reason, UserSession.EndReason.EXPIRED)

    def test_admin_sees_target_sessions(self):
        self._login(SAFARI_IPHONE)
        admin = User.objects.create_user(username="sess_admin", password=PASSWORD)
        profile = UserProfile.objects.get(user=admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        admin_client = Client()
        admin_client.force_login(admin)
        response = admin_client.get(reverse("control_center:user_detail", args=[self.user.pk]))
        self.assertContains(response, "iOS 17 · Safari 17")
