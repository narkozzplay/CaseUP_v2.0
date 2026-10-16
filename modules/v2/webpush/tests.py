from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.services import notify

from . import services
from .models import PushLog, PushSettings, PushSubscription, VapidKey

User = get_user_model()
WEBPUSH = "pywebpush.webpush"
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPkey", "auth": "authsecret"}}


class FakeResponse:
    def __init__(self, code):
        self.status_code = code


class PushTestCase(TestCase):
    def setUp(self):
        cache.clear()
        services._vapid_cache.clear()
        self.user = User.objects.create_user(username="pushy", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    def subscribe(self, data=SUB):
        return self.client.post(reverse("webpush:subscribe"), json.dumps(data), content_type="application/json", HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Chrome/130")


class SubscriptionTests(PushTestCase):
    def test_key_is_created_once_and_subscribe_stores_the_device(self):
        key = self.client.get(reverse("webpush:key")).json()["publicKey"]
        self.assertEqual(len(key), 87)  # 65-byte uncompressed P-256 point, base64url
        services._vapid_cache.clear()
        self.assertEqual(self.client.get(reverse("webpush:key")).json()["publicKey"], key)
        self.assertEqual(VapidKey.objects.count(), 1)

        self.assertEqual(self.subscribe().json(), {"ok": True})
        device = PushSubscription.objects.get()
        self.assertEqual(device.device_label, "Android · Chrome")
        self.assertEqual(self.subscribe({"endpoint": "http://insecure", "keys": {}}).status_code, 400)

        other = User.objects.create_user(username="other", password="x")
        client = Client()
        client.force_login(other)
        client.post(reverse("webpush:subscribe"), json.dumps(SUB), content_type="application/json")
        self.assertEqual(PushSubscription.objects.get().user, other)  # same browser, another account signed in

    def test_unsubscribe(self):
        self.subscribe()
        self.client.post(reverse("webpush:unsubscribe"), json.dumps({"endpoint": SUB["endpoint"]}), content_type="application/json", HTTP_ACCEPT="application/json")
        self.assertFalse(PushSubscription.objects.exists())

    def test_service_worker_and_manifest(self):
        sw = self.client.get("/sw.js")
        self.assertEqual(sw["Service-Worker-Allowed"], "/")
        self.assertIn("showNotification", sw.content.decode())
        manifest = self.client.get("/manifest.webmanifest").json()
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in manifest["icons"]))


class DeliveryTests(PushTestCase):
    def setUp(self):
        super().setUp()
        self.subscribe()

    def test_notifications_are_mirrored_to_devices(self):
        with mock.patch(WEBPUSH) as push, self.captureOnCommitCallbacks(execute=True):
            notify(self.user, "balance_topup_succeeded", object_id=7, payload={"amount": "500.00"})
        self.assertEqual(push.call_count, 1)
        payload = json.loads(push.call_args.kwargs["data"])
        self.assertIn("500", payload["title"] + payload["body"])
        self.assertEqual(payload["tag"], "balance_topup_succeeded:7")
        self.assertEqual(PushLog.objects.get().category, "balance")

    def test_switched_off_category_is_skipped(self):
        services.save_categories(self.user, ["market", "security"])
        with mock.patch(WEBPUSH) as push, self.captureOnCommitCallbacks(execute=True):
            notify(self.user, "balance_topup_succeeded", object_id=8, payload={"amount": "10.00"})
        push.assert_not_called()

    def test_expired_subscription_is_removed(self):
        from pywebpush import WebPushException

        with mock.patch(WEBPUSH, side_effect=WebPushException("gone", response=FakeResponse(410))):
            self.assertEqual(services.deliver(self.user.pk, title="x", category="market"), 0)
        self.assertFalse(PushSubscription.objects.exists())
        self.assertEqual(PushLog.objects.get().status, PushLog.Status.GONE)

    def test_direct_messages_push_the_other_member_once_a_minute(self):
        from modules.v2.social.services import create_space_message, get_or_create_direct_space

        sender = User.objects.create_user(username="sender", password="x")
        space = get_or_create_direct_space(sender, self.user)
        with mock.patch(WEBPUSH) as push, self.captureOnCommitCallbacks(execute=True):
            create_space_message(sender, space, "Привет, трейд отправил")
            create_space_message(sender, space, "Проверь Steam")
        self.assertEqual(push.call_count, 1)
        self.assertIn("sender", json.loads(push.call_args.kwargs["data"])["title"])

    def test_test_push_endpoint(self):
        with mock.patch(WEBPUSH):
            self.assertEqual(self.client.post(reverse("webpush:test")).json(), {"sent": 1})


class PromptTests(PushTestCase):
    def test_prompt_after_an_event_until_dismissed(self):
        services.request_prompt(self.user, "market_buy")
        page = self.client.get(reverse("profiles:profile_settings"))
        self.assertContains(page, "Включить уведомления?")
        self.assertContains(page, "когда он отправит трейд")
        self.assertContains(page, 'id="settings-push"')

        self.client.post(reverse("webpush:dismiss_prompt"))
        self.assertNotContains(self.client.get(reverse("profiles:profile_settings")), "Включить уведомления?")
        services.request_prompt(self.user, "topup")
        self.assertIsNone(services.prompt_for(self.user))  # snoozed for 3 days
        PushSettings.objects.filter(user=self.user).update(prompt_snoozed_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.prompt_for(self.user)["reason"], "topup")

    def test_subscribed_users_are_not_prompted(self):
        self.subscribe()
        services.request_prompt(self.user, "topup")
        self.assertIsNone(services.prompt_for(self.user))

    def test_events_raise_the_prompt(self):
        from modules.v2.social_friends.services import send_friend_request

        friend = User.objects.create_user(username="friend", password="x")
        with self.captureOnCommitCallbacks(execute=True):
            send_friend_request(self.user, friend)
        self.assertEqual(services.prompt_for(self.user)["reason"], "friend_request")
