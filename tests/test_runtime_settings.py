from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from config.settings import base
from modules.v2.profiles.services.presence import touch_user_last_seen


class LoggingSettingsTests(TestCase):
    def test_django_errors_reach_console_without_debug(self):
        # Django's default config prints django.request only when DEBUG=True.
        self.assertEqual(base.LOGGING["loggers"]["django"]["handlers"], ["console"])
        self.assertNotIn("filters", base.LOGGING["handlers"]["console"])


class PresenceCacheOutageTests(TestCase):
    def setUp(self):
        # LocMem survives between tests and user ids repeat after rollbacks.
        cache.clear()

    def test_last_seen_touch_survives_cache_outage(self):
        user = get_user_model().objects.create_user(username="presence_cache_down", password="StrongPass123")

        with patch("modules.v2.profiles.services.presence.cache.get", side_effect=ConnectionError("redis down")):
            with self.assertLogs("modules.v2.profiles.services.presence", level="WARNING"):
                self.assertFalse(touch_user_last_seen(user))

        user.profile_v2.refresh_from_db()
        self.assertIsNone(user.profile_v2.last_seen_at)

    def test_last_seen_is_stored_when_cache_write_fails(self):
        user = get_user_model().objects.create_user(username="presence_cache_set_down", password="StrongPass123")

        with patch("modules.v2.profiles.services.presence.cache.set", side_effect=ConnectionError("redis down")):
            with self.assertLogs("modules.v2.profiles.services.presence", level="WARNING"):
                self.assertTrue(touch_user_last_seen(user))

        user.profile_v2.refresh_from_db()
        self.assertIsNotNone(user.profile_v2.last_seen_at)
