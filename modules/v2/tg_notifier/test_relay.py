from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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
