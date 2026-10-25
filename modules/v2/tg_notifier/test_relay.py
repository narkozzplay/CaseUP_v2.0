from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.steam_catalog.models import SteamItem, UserPriceAlertEvent, UserPriceAlertRule

from . import services
from .models import TelegramNotifierConfig

User = get_user_model()
RELAY = "https://relay.case-up.ru/telegram_relay.php"


def fake_response(payload: dict):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    return response


class RelayTests(TestCase):
    def setUp(self):
        self.config = TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEFabcdef", default_chat_id="-100555", public_base_url="https://case-up.ru")

    def test_direct_when_no_relay(self):
        with patch("urllib.request.urlopen", return_value=fake_response({"ok": True})) as urlopen:
            services.send_message(text="hi")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123456:ABCDEFabcdef/sendMessage")
        self.assertIn("case-up.ru/telegram/webhook/", services.build_webhook_url(self.config))

    @override_settings(TELEGRAM_RELAY_URL=RELAY, TELEGRAM_RELAY_TOKEN="", STEAM_PRICE_RELAY_TOKEN="shared-relay-key")
    def test_calls_and_webhook_go_through_the_relay(self):
        with patch("urllib.request.urlopen", return_value=fake_response({"ok": True, "result": True})) as urlopen:
            services.send_message(text="hi", chat_id="42")
            services.register_webhook(self.config)
        send, hook = (call.args[0] for call in urlopen.call_args_list)
        self.assertEqual(send.full_url, f"{RELAY}?method=sendMessage")
        self.assertEqual(send.get_header("X-relay-token"), "shared-relay-key")  # one key for both relays
        self.assertEqual(send.get_header("X-bot-token"), "123456:ABCDEFabcdef")
        self.assertNotIn("123456", send.full_url)
        self.assertEqual(json.loads(send.data)["chat_id"], "42")
        self.assertEqual(json.loads(hook.data)["url"], f"{RELAY}?inbound={self.config.webhook_secret}")


class SiteDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alerted", password="x")
        self.item = SteamItem.objects.create(market_hash_name="Recoil Case", name="Recoil Case", item_type="case", price_rub=Decimal("170"))

    def event(self, transport):
        rule = UserPriceAlertRule.objects.create(user=self.user, name="Кейсы вниз", scope_type=UserPriceAlertRule.ScopeType.ITEM, item=self.item, direction=UserPriceAlertRule.Direction.DOWN, status=UserPriceAlertRule.Status.ACTIVE, transport=transport)
        return UserPriceAlertEvent.objects.create(
            rule=rule, user=self.user, item=self.item, transport=transport, direction=UserPriceAlertRule.Direction.DOWN,
            reference_point=rule.reference_point, reference_at=timezone.now() - timedelta(hours=2), current_at=timezone.now(),
            window_started_at=timezone.now() - timedelta(hours=3), baseline_price_rub=Decimal("210.00"), current_price_rub=Decimal("170.00"),
            change_rub=Decimal("-40.00"), change_percent=Decimal("-19.05"), dedupe_key=f"site-{transport}",
        )

    def test_new_rules_alert_on_the_site(self):
        from modules.v2.steam_catalog.services.user_alerts import dispatch_pending_user_price_alerts

        self.assertEqual(UserPriceAlertRule().transport, UserPriceAlertRule.Transport.SITE)
        event = self.event(UserPriceAlertRule.Transport.SITE)
        self.assertEqual(dispatch_pending_user_price_alerts(), 1)
        event.refresh_from_db()
        self.assertEqual((event.delivery_status, event.sent_to), (UserPriceAlertEvent.Status.SENT, "site"))
        notification = Notification.objects.get(recipient=self.user, kind="steam_price_alert")
        self.assertIn("-19.05%", notification.payload["title"])

    def test_both_survives_a_telegram_failure(self):
        from modules.v2.steam_catalog.services.user_alerts import dispatch_pending_user_price_alerts

        event = self.event(UserPriceAlertRule.Transport.BOTH)
        self.assertEqual(dispatch_pending_user_price_alerts(), 1)  # no chat linked, but the site got it
        event.refresh_from_db()
        self.assertEqual(event.delivery_status, UserPriceAlertEvent.Status.SENT)
        self.assertIn("chat id", event.error_message)


class HealthCheckTests(TestCase):
    @override_settings(TELEGRAM_RELAY_URL=RELAY, TELEGRAM_RELAY_TOKEN="relay-key")
    def test_live_check_goes_through_the_relay_with_the_bot_token_in_use(self):
        from modules.v2.system.services import _check_telegram, _telegram_bot_token

        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="777777:NEWTOKENnewtokenNEWTOKENnewtoken")
        self.assertEqual(_telegram_bot_token(), "777777:NEWTOKENnewtokenNEWTOKENnewtoken")
        with patch("urllib.request.urlopen", return_value=fake_response({"ok": True, "result": {"username": "caseup_cs2cases_bot"}})) as urlopen:
            status, message = _check_telegram(_telegram_bot_token())
        self.assertEqual(urlopen.call_args.args[0].full_url, f"{RELAY}?method=getMe")
        self.assertEqual(status, "up")
        self.assertIn("@caseup_cs2cases_bot", message)

    @override_settings(TELEGRAM_RELAY_URL=RELAY, TELEGRAM_RELAY_TOKEN="relay-key")
    def test_rejected_token_is_explained(self):
        from urllib.error import HTTPError

        from modules.v2.system.services import _check_telegram

        error = HTTPError(RELAY, 401, "Unauthorized", {}, None)
        error.read = lambda: b'{"ok":false,"error_code":401}'
        with patch("urllib.request.urlopen", side_effect=error):
            status, message = _check_telegram("1:bad")
        self.assertEqual(status, "degraded")
        self.assertIn("BotFather", message)


class SettingsPageTests(TestCase):
    def setUp(self):
        from modules.v2.profiles.models import UserProfile, UserRole

        self.admin = User.objects.create_user(username="tg_admin", password="x")
        UserProfile.objects.filter(user=self.admin).update(role=UserRole.ADMIN)
        self.client.force_login(self.admin)
        self.config = TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEFabcdefABCDEFabcdefABCDEFabc", bot_username="caseup_bot", public_base_url="https://case-up.ru")

    def test_page_explains_state_and_hides_the_webhook_secret(self):
        page = self.client.get(reverse("tg_notifier:settings"))
        self.assertContains(page, "Состояние")
        self.assertContains(page, "Приём сообщений от бота")
        self.assertNotContains(page, self.config.webhook_secret)

    def test_check_webhook_points_at_a_wrong_address(self):
        TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(updates_mode=TelegramNotifierConfig.UpdatesMode.WEBHOOK)
        info = services.TelegramWebhookInfoResult(url="https://case-up.ru/telegram/webhook/old/", pending_update_count=3, last_error_date=1760000000, last_error_message="Connection timed out", max_connections=40, ip_address="", has_custom_certificate=False)
        with patch("modules.v2.tg_notifier.views.get_webhook_info", return_value=info):
            page = self.client.post(reverse("tg_notifier:settings"), {"action": "check_webhook"})
        self.assertContains(page, "не тот адрес")
        self.assertContains(page, "Connection timed out")

    def test_check_bot(self):
        with patch("urllib.request.urlopen", return_value=fake_response({"ok": True, "result": {"username": "caseup_bot"}})):
            page = self.client.post(reverse("tg_notifier:settings"), {"action": "check_bot"})
        self.assertContains(page, "Бот @caseup_bot отвечает")


class PollingTests(TestCase):
    """«Опрос»: Telegram cannot reach the relay hosting, so the site fetches /start messages itself."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.config = TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEFabcdefABCDEFabcdefABCDEFabc", bot_username="caseup_bot")
        self.user = User.objects.create_user(username="tg_poll_user", password="x")

    def _update(self, update_id: int, text: str, age_seconds: int = 5) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "date": int(timezone.now().timestamp()) - age_seconds,
                "chat": {"id": 7151390596, "type": "private"},
                "from": {"id": 111, "username": "narkozzplay", "first_name": "Fedor"},
                "text": text,
            },
        }

    def test_start_message_links_profile_and_moves_offset(self):
        from modules.v2.profiles.models import UserProfile

        from . import updates
        from .models import TelegramLinkToken

        link = TelegramLinkToken.objects.create(user=self.user)
        calls = []

        def api(*, endpoint, payload, timeout=15):
            calls.append((endpoint.rsplit("/", 1)[-1], payload))
            return {"ok": True, "result": [self._update(500, f"/start caseup_{link.token}")]}

        with patch.object(updates, "_telegram_api_request", side_effect=api), patch.object(updates, "send_message") as send:
            worked, handled = updates.poll_once()

        self.assertEqual((worked, handled), (True, 1))
        self.assertEqual(calls[0][0], "getUpdates")
        self.assertEqual(calls[0][1]["offset"], 1)
        self.assertEqual(UserProfile.objects.get(user=self.user).telegram_chat_id, "7151390596")
        send.assert_called_once()
        self.config.refresh_from_db()
        self.assertEqual(self.config.polling_offset, 500)
        self.assertFalse(updates.get_poll_status()["error"])

    def test_dropped_connection_is_a_notifier_error_and_poll_status_tracks_the_streak(self):
        import http.client

        from . import services, updates

        with patch.object(services.urllib.request, "urlopen", side_effect=http.client.RemoteDisconnected("Remote end closed connection without response")):
            with self.assertRaises(services.TelegramNotifierError):
                updates.poll_once()
        first = updates.get_poll_status()
        self.assertIn("RemoteDisconnected", first["error"])
        self.assertIsNotNone(first["failing_since"])

        with patch.object(updates, "_telegram_api_request", side_effect=services.TelegramNotifierError("Telegram API HTTP 502")):
            with self.assertRaises(services.TelegramNotifierError):
                updates.poll_once()
        self.assertEqual(updates.get_poll_status()["failing_since"], first["failing_since"])

        with patch.object(updates, "_telegram_api_request", return_value={"ok": True, "result": []}):
            updates.poll_once()
        status = updates.get_poll_status()
        self.assertIsNone(status["failing_since"])
        ok_since = status["ok_since"]
        self.assertIsNotNone(ok_since)

        with patch.object(updates, "_telegram_api_request", return_value={"ok": True, "result": []}):
            updates.poll_once()
        self.assertEqual(updates.get_poll_status()["ok_since"], ok_since)

        # A long gap without polls (poller was down) starts a new clean run.
        stale = dict(updates.get_poll_status(), at=timezone.now() - timedelta(minutes=30))
        cache.set(updates.POLL_STATUS_KEY, stale)
        with patch.object(updates, "_telegram_api_request", return_value={"ok": True, "result": []}):
            updates.poll_once()
        self.assertGreater(updates.get_poll_status()["ok_since"], ok_since)

    def test_stale_message_is_acknowledged_without_reply(self):
        from . import updates

        with patch.object(updates, "_telegram_api_request", return_value={"ok": True, "result": [self._update(77, "/start caseup_old", age_seconds=3 * 3600)]}), patch.object(updates, "send_message") as send:
            self.assertEqual(updates.poll_once(), (True, 0))
        send.assert_not_called()
        self.config.refresh_from_db()
        self.assertEqual(self.config.polling_offset, 77)

    def test_registered_webhook_is_removed(self):
        from . import updates

        methods = []

        def api(*, endpoint, payload, timeout=15):
            method = endpoint.rsplit("/", 1)[-1]
            methods.append(method)
            if method == "getUpdates":
                raise services.TelegramNotifierError("Telegram API HTTP 409: Conflict: can't use getUpdates method while webhook is active")
            return {"ok": True, "result": True}

        with patch.object(updates, "_telegram_api_request", side_effect=api):
            self.assertEqual(updates.poll_once(), (True, 0))
        self.assertEqual(methods, ["getUpdates", "deleteWebhook"])

    def test_idle_in_webhook_mode_or_when_disabled(self):
        from . import updates

        with patch.object(updates, "_telegram_api_request") as api:
            TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(updates_mode=TelegramNotifierConfig.UpdatesMode.WEBHOOK)
            self.assertEqual(updates.poll_once(), (False, 0))
            TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(updates_mode=TelegramNotifierConfig.UpdatesMode.POLLING, is_enabled=False)
            self.assertEqual(updates.poll_once(), (False, 0))
        api.assert_not_called()

    def test_error_is_shown_on_settings_page(self):
        from django.core.management import call_command

        from modules.v2.profiles.models import UserProfile, UserRole

        from . import updates

        admin = User.objects.create_user(username="tg_poll_admin", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("tg_notifier:settings")), "ещё не запускался")

        with patch.object(updates, "_telegram_api_request", side_effect=services.TelegramNotifierError("Telegram API HTTP 401: Unauthorized")):
            # close_old_connections would drop the test transaction's connection.
            with patch("modules.v2.tg_notifier.management.commands.telegram_poll.close_old_connections"), self.assertRaises(services.TelegramNotifierError):
                call_command("telegram_poll", "--once")
        page = self.client.get(reverse("tg_notifier:settings"))
        self.assertContains(page, "Последний опрос")
        self.assertContains(page, "401")

        refused = self.client.post(reverse("tg_notifier:settings"), {"action": "register_webhook"}, follow=True)
        self.assertContains(refused, "Опрос")
