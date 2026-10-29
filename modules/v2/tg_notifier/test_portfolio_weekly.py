from __future__ import annotations

import json

from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory
from modules.v2.steam_trading.models import SteamPurchase
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription

from . import portfolio_weekly
from .models import TelegramNotifierConfig, TelegramUserPrefs

User = get_user_model()
API = "modules.v2.tg_notifier.services._telegram_api_request"
CHAT = "5550002"
MONDAY = datetime(2026, 11, 2)


def monday_at(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(MONDAY.replace(hour=hour, minute=minute))


class WeeklyPortfolioTests(TestCase):
    def setUp(self):
        cache.clear()
        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEF", public_base_url="https://case-up.ru")
        self.user = User.objects.create_user(username="weekly", password="x")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.telegram_chat_id = CHAT
        profile.save(update_fields=["telegram_chat_id"])
        self.case = SteamItem.objects.create(
            market_hash_name="Fracture Case", name="Fracture Case", family_name="Fracture Case",
            item_type=SteamItem.ItemType.CASE, price_rub=Decimal("30.00"), is_active=True,
        )
        SteamPurchase.objects.create(
            item=self.case, purchased_at=monday_at(9) - timedelta(days=20), quantity=10,
            purchase_price_rub=Decimal("20.00"), created_by=self.user,
        )
        row = SteamItemPriceHistory.objects.create(item=self.case, price_rub=Decimal("25.00"), source="queue")
        SteamItemPriceHistory.objects.filter(pk=row.pk).update(created_at=monday_at(8) - timedelta(days=9))
        self.calls = []
        patcher = patch(API, side_effect=self._api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _api(self, *, endpoint, payload, timeout=15, files=None):
        self.calls.append((endpoint.rsplit("/", 1)[1], payload, files))
        return {"ok": True, "result": {"message_id": 1}}

    def _pro(self):
        UserSubscription.objects.create(user=self.user, plan=SubscriptionPlan.objects.get(code="site_pro"))

    def test_sent_on_monday_at_digest_time_once_with_picture(self):
        self._pro()
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10))
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(9)), 0)
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 1)
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(18)), 0)
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5) + timedelta(days=1)), 0)  # Tuesday

        method, payload, files = self.calls[-1]
        self.assertEqual(method, "sendPhoto")
        self.assertTrue(files["photo"][1].startswith(b"\x89PNG"))
        self.assertIn("Портфель за неделю", payload["caption"])
        self.assertIn("+50 ₽", payload["caption"])  # 10 × (30 − 25)
        self.assertIn("Fracture Case ×10", payload["caption"])
        self.assertEqual(json.loads(payload["reply_markup"])["inline_keyboard"][0][0]["text"], "Открыть портфель")

    def test_sent_to_investors_who_never_saved_bot_settings(self):
        self._pro()
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 1)
        self.assertTrue(TelegramUserPrefs.objects.get(user=self.user).weekly_portfolio_enabled)

    def test_not_sent_without_pro(self):
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10))
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 0)
        self.assertEqual(self.calls, [])

    def test_turned_off_in_settings(self):
        self._pro()
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10), weekly_portfolio_enabled=False)
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 0)

    def test_falls_back_to_text_when_the_picture_fails(self):
        from .services import TelegramNotifierError

        self._pro()
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10))
        with patch("modules.v2.tg_notifier.portfolio_weekly.send_photo", side_effect=TelegramNotifierError("400")):
            self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 1)
        self.assertEqual(self.calls[-1][0], "sendMessage")

    def test_retried_after_both_sends_fail(self):
        from .services import TelegramNotifierError

        self._pro()
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10))
        with patch("modules.v2.tg_notifier.portfolio_weekly.send_photo", side_effect=TelegramNotifierError("502")), \
                patch("modules.v2.tg_notifier.portfolio_weekly.send_message", side_effect=TelegramNotifierError("502")):
            self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 0)
        self.assertIsNone(TelegramUserPrefs.objects.get(user=self.user).weekly_portfolio_last_sent_on)
        self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 10)), 1)

    def test_released_when_the_chart_breaks(self):
        self._pro()
        TelegramUserPrefs.objects.create(user=self.user, digest_time=time(10))
        with patch("modules.v2.tg_notifier.portfolio_weekly.render_week_chart", side_effect=OSError("font")):
            self.assertEqual(portfolio_weekly.send_due_weekly(now=monday_at(10, 5)), 0)
        self.assertIsNone(TelegramUserPrefs.objects.get(user=self.user).weekly_portfolio_last_sent_on)
