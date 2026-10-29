from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from modules.v2.market.models import Listing
from modules.v2.profiles.models import UserProfile
from modules.v2.steam_catalog.models import SteamItem, SteamItemFavorite, SteamItemPriceHistory, UserPriceAlertEvent, UserPriceAlertRule

from . import bot, delivery
from .models import TelegramDeferredNotice, TelegramNotifierConfig, TelegramUserPrefs
from .updates import handle_update, poll_updates

User = get_user_model()
API = "modules.v2.tg_notifier.services._telegram_api_request"
CHAT = "5550001"


def at_msk(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(2026, 10, 29, hour, minute))


class BotTestCase(TestCase):
    def setUp(self):
        cache.clear()
        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEF", public_base_url="https://case-up.ru")
        self.user = User.objects.create_user(username="trader", password="x")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.telegram_chat_id = CHAT
        profile.save(update_fields=["telegram_chat_id"])
        self.item = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)", name="AK-47 | Redline (Field-Tested)", family_name="AK-47 | Redline",
            item_type=SteamItem.ItemType.SKIN, price_rub=Decimal("1100.00"), wear_order=3,
        )
        self.calls = []
        patcher = patch(API, side_effect=self._api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _api(self, *, endpoint, payload, timeout=15, files=None):
        method = endpoint.rsplit("/", 1)[1]
        self.calls.append((method, payload))
        return {"ok": True, "result": [] if method == "getUpdates" else {"message_id": 1}}

    def sent(self, method="sendMessage"):
        return [payload for name, payload in self.calls if name == method]

    def history(self, item, price, ago: timedelta):
        row = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal(price), source="queue")
        SteamItemPriceHistory.objects.filter(pk=row.pk).update(created_at=timezone.now() - ago)
        return row

    def message(self, text, chat_id=CHAT):
        return handle_update({"update_id": 1, "message": {"chat": {"id": chat_id, "type": "private"}, "text": text, "date": int(timezone.now().timestamp())}})

    def press(self, data, chat_id=CHAT):
        return handle_update({"update_id": 2, "callback_query": {"id": "cb1", "data": data, "message": {"message_id": 77, "chat": {"id": chat_id, "type": "private"}}}})


class CommandTests(BotTestCase):
    def test_parse_command(self):
        self.assertEqual(bot.parse_command("/price@CaseUpBot AK-47 Redline"), ("price", "AK-47 Redline"))
        self.assertEqual(bot.parse_command(bot.BTN_ALERTS), ("alerts", ""))
        self.assertIsNone(bot.parse_command("redline"))

    def test_unlinked_chat_is_sent_to_the_profile(self):
        self.message("/price redline", chat_id="999")
        reply = self.sent()[-1]
        self.assertIn("привяжите Telegram", reply["text"])
        self.assertIn("case-up.ru/profile", reply["reply_markup"]["inline_keyboard"][0][0]["url"])

    def test_price_card_with_changes_and_buttons(self):
        self.history(self.item, "1000", timedelta(days=2))
        self.history(self.item, "1100", timedelta(hours=1))
        seller = User.objects.create_user(username="seller", password="x")
        Listing.objects.create(seller=seller, asset_id="1", market_hash_name=self.item.market_hash_name, price=Decimal("1050"))

        self.message(f"/price {self.item.market_hash_name}")
        reply = self.sent()[-1]
        self.assertIn("1 100 ₽", reply["text"])
        self.assertIn("24 ч: +10.0%, +100 ₽", reply["text"])
        self.assertIn("30 д: нет данных", reply["text"])
        buttons = [button for row in reply["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(buttons[0]["callback_data"], f"fv:{self.item.pk}")
        self.assertIn("от 1 050 ₽", buttons[1]["text"])

    def test_no_market_button_without_lots(self):
        self.message(f"/price {self.item.market_hash_name}")
        rows = self.sent()[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(rows), 1)

    def test_several_matches_become_buttons(self):
        other = SteamItem.objects.create(market_hash_name="AK-47 | Redline (Minimal Wear)", name="AK-47 | Redline (Minimal Wear)", family_name="AK-47 | Redline", item_type="skin", price_rub=Decimal("1500"), wear_order=2)
        self.message("/price redline ak")
        rows = self.sent()[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual({row[0]["callback_data"] for row in rows}, {f"it:{self.item.pk}", f"it:{other.pk}"})

    def test_price_button_waits_for_the_name(self):
        self.message(bot.BTN_PRICE)
        self.assertIn("Напишите название", self.sent()[-1]["text"])
        self.message("Redline (Field-Tested)")
        self.assertIn("AK-47 | Redline (Field-Tested)", self.sent()[-1]["text"])
        self.message("Redline")
        self.assertIn("/price", self.sent()[-1]["text"])  # without the button, plain text is not a search

    def test_favorite_button_toggles_and_redraws(self):
        self.press(f"fv:{self.item.pk}")
        self.assertTrue(SteamItemFavorite.objects.filter(user=self.user).exists())
        markup = self.sent("editMessageReplyMarkup")[-1]["reply_markup"]
        self.assertIn("Убрать", markup["inline_keyboard"][0][0]["text"])
        self.assertEqual(self.sent("answerCallbackQuery")[-1]["text"], "Добавлено в избранное")
        self.press(f"fv:{self.item.pk}")
        self.assertFalse(SteamItemFavorite.objects.filter(user=self.user).exists())

    def test_favorites_and_balance(self):
        SteamItemFavorite.objects.create(user=self.user, family_key="AK-47 | Redline", item=self.item)
        self.message("/favorites")
        self.assertIn("1. AK-47 | Redline — 1 100 ₽", self.sent()[-1]["text"])
        UserProfile.objects.filter(user=self.user).update(balance_coins=Decimal("250.50"), market_balance_coins=Decimal("90"))
        self.message(bot.BTN_BALANCE)
        reply = self.sent()[-1]
        self.assertIn("250.50 ₽", reply["text"])
        self.assertEqual([button["text"] for button in reply["reply_markup"]["inline_keyboard"][0]], ["Пополнить", "Вывести"])

    def test_empty_portfolio(self):
        self.message("/portfolio")
        self.assertIn("нет покупок", self.sent()[-1]["text"])

    def test_rate_limit(self):
        for _ in range(bot.COMMANDS_PER_MINUTE + 3):
            self.message("/help")
        texts = [payload["text"] for payload in self.sent()]
        self.assertEqual(len(texts), bot.COMMANDS_PER_MINUTE + 1)
        self.assertIn("Слишком много", texts[-1])

    def test_group_chats_are_ignored(self):
        handle_update({"message": {"chat": {"id": -100, "type": "group"}, "text": "/price ak"}})
        self.assertEqual(self.sent(), [])

    def test_a_group_cannot_be_linked(self):
        from .services import issue_link_token_for_user

        token = issue_link_token_for_user(self.user)
        handle_update({"message": {"chat": {"id": -100, "type": "supergroup"}, "text": f"/start caseup_{token.token}"}})
        self.assertEqual(UserProfile.objects.get(user=self.user).telegram_chat_id, CHAT)

    def test_polling_asks_for_button_presses(self):
        config = TelegramNotifierConfig.objects.get()
        with patch("modules.v2.tg_notifier.updates._telegram_api_request", side_effect=self._api):
            poll_updates(config, long_poll=0)
        name, payload = self.calls[-1]
        self.assertEqual(name, "getUpdates")
        self.assertIn("callback_query", payload["allowed_updates"])


class AlertButtonTests(BotTestCase):
    def rule(self, **extra):
        values = {"user": self.user, "name": "Редлайн вниз", "scope_type": UserPriceAlertRule.ScopeType.ITEM, "item": self.item, "direction": UserPriceAlertRule.Direction.DOWN, "min_change_percent": Decimal("5"), "threshold_mode": UserPriceAlertRule.ThresholdMode.PERCENT, "status": UserPriceAlertRule.Status.ACTIVE}
        values.update(extra)
        return UserPriceAlertRule.objects.create(**values)

    def test_alerts_list_toggle_and_delete(self):
        rule = self.rule()
        self.message("/alerts")
        reply = self.sent()[-1]
        self.assertIn("Редлайн вниз", reply["text"])
        self.assertIn("▼ падение на 5%", reply["text"])
        self.press(f"at:{rule.pk}:1")
        rule.refresh_from_db()
        self.assertEqual(rule.status, UserPriceAlertRule.Status.DISABLED)
        self.assertIn("▶ Включить", str(self.sent("editMessageText")[-1]["reply_markup"]))
        self.press(f"ad:{rule.pk}:1")
        self.assertIn("Удалить алерт", self.sent("editMessageText")[-1]["text"])
        self.press(f"aD:{rule.pk}:1")
        self.assertFalse(UserPriceAlertRule.objects.filter(pk=rule.pk).exists())

    def test_foreign_rules_are_untouchable(self):
        stranger = User.objects.create_user(username="stranger", password="x")
        rule = self.rule(user=stranger)
        self.press(f"ax:{rule.pk}")
        self.press(f"aD:{rule.pk}:1")
        rule.refresh_from_db()
        self.assertEqual(rule.status, UserPriceAlertRule.Status.ACTIVE)

    def test_switching_on_respects_the_plan_limit(self):
        rule = self.rule(status=UserPriceAlertRule.Status.DISABLED)
        with patch("modules.v2.steam_catalog.forms.alert_limit_error", return_value="Активных алертов уже 3 из 3"):
            self.press(f"ae:{rule.pk}")
        rule.refresh_from_db()
        self.assertEqual(rule.status, UserPriceAlertRule.Status.DISABLED)
        answer = self.sent("answerCallbackQuery")[-1]
        self.assertTrue(answer["show_alert"])
        self.assertIn("3 из 3", answer["text"])

    def test_alert_notice_has_buttons(self):
        from modules.v2.steam_catalog.services.user_alerts import dispatch_pending_user_price_alerts

        TelegramUserPrefs.objects.create(user=self.user, quiet_enabled=False)
        rule = self.rule(transport=UserPriceAlertRule.Transport.TELEGRAM)
        now = timezone.now()
        UserPriceAlertEvent.objects.create(
            rule=rule, user=self.user, item=self.item, transport=rule.transport, direction="down", reference_point=rule.reference_point,
            reference_at=now - timedelta(hours=3), current_at=now, window_started_at=now - timedelta(hours=3),
            baseline_price_rub=Decimal("1200"), current_price_rub=Decimal("1100"), change_rub=Decimal("-100"), change_percent=Decimal("-8.33"), dedupe_key="b1",
        )
        self.assertEqual(dispatch_pending_user_price_alerts(), 1)
        reply = self.sent()[-1]
        self.assertIn("▼ Падение", reply["text"])
        rows = reply["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[0][1]["callback_data"], f"ax:{rule.pk}")
        self.assertIn(f"/alerts/rules/{rule.pk}/edit/", rows[1][0]["url"])
        self.press(f"ax:{rule.pk}")
        rule.refresh_from_db()
        self.assertEqual(rule.status, UserPriceAlertRule.Status.DISABLED)
        self.assertEqual(self.sent("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"], f"ae:{rule.pk}")


class TargetAlertTests(BotTestCase):
    def test_fires_once_then_needs_a_new_crossing(self):
        from modules.v2.steam_catalog.services.user_alerts import evaluate_user_price_alerts_for_item_type

        rule = UserPriceAlertRule.objects.create(
            user=self.user, name="Купить дешевле", kind=UserPriceAlertRule.Kind.TARGET, target_price_rub=Decimal("1000"),
            scope_type=UserPriceAlertRule.ScopeType.ITEM, item=self.item, direction=UserPriceAlertRule.Direction.DOWN, status=UserPriceAlertRule.Status.ACTIVE,
        )
        rule.full_clean()
        self.history(self.item, "1100", timedelta(hours=3))
        self.assertEqual(evaluate_user_price_alerts_for_item_type("skin"), 0)
        self.history(self.item, "990", timedelta(hours=2))
        self.assertEqual(evaluate_user_price_alerts_for_item_type("skin"), 1)
        rule.refresh_from_db()
        self.assertEqual(rule.status, UserPriceAlertRule.Status.DISABLED)

        rule.status = UserPriceAlertRule.Status.ACTIVE
        rule.save()
        self.history(self.item, "980", timedelta(hours=1))
        self.assertEqual(evaluate_user_price_alerts_for_item_type("skin"), 0)  # still below: no new crossing
        self.history(self.item, "1050", timedelta(minutes=30))
        self.history(self.item, "995", timedelta(minutes=10))
        self.assertEqual(evaluate_user_price_alerts_for_item_type("skin"), 1)

    def test_validation(self):
        from django.core.exceptions import ValidationError

        rule = UserPriceAlertRule(user=self.user, name="x", kind=UserPriceAlertRule.Kind.TARGET, scope_type=UserPriceAlertRule.ScopeType.ITEM, item=self.item, direction=UserPriceAlertRule.Direction.BOTH, target_price_rub=Decimal("10"))
        with self.assertRaises(ValidationError):
            rule.full_clean()


class DeliveryTests(BotTestCase):
    def test_quiet_hours_hold_regular_notices_and_flush_them_together(self):
        TelegramUserPrefs.objects.create(user=self.user, quiet_from=time(23), quiet_to=time(9))
        markup = {"inline_keyboard": [[{"text": "⏸ Выключить", "callback_data": "ax:1"}]]}
        self.assertEqual(delivery.deliver_notice(self.user, "первое", reply_markup=markup, now=at_msk(2)), delivery.DEFERRED)
        self.assertEqual(delivery.deliver_notice(self.user, "второе", now=at_msk(3)), delivery.DEFERRED)
        self.assertEqual(self.sent(), [])
        self.assertEqual(delivery.flush_deferred(now=at_msk(8, 59)), 0)
        self.assertEqual(delivery.flush_deferred(now=at_msk(9, 1)), 1)
        first, second = self.sent()
        self.assertEqual((first["text"], first["reply_markup"]), ("первое", markup))  # buttons survive the night
        self.assertNotIn("disable_notification", first)
        self.assertEqual(second["text"], "второе")
        self.assertTrue(second["disable_notification"])
        self.assertFalse(TelegramDeferredNotice.objects.exists())

    def test_night_notices_count_towards_the_daily_cap(self):
        TelegramUserPrefs.objects.create(user=self.user, daily_limit=2)
        for index in range(4):
            delivery.deliver_notice(self.user, f"n{index}", now=at_msk(2))
        delivery.flush_deferred(now=at_msk(9, 5))
        texts = [payload["text"] for payload in self.sent()]
        self.assertEqual(texts[:2], ["n0", "n1"])
        self.assertIn("Ещё 2", texts[2])
        self.assertEqual(delivery.deliver_notice(self.user, "днём", now=at_msk(12)), delivery.LIMITED)

    def test_digest_is_retried_after_a_failure(self):
        from .services import TelegramNotifierError

        TelegramUserPrefs.objects.create(user=self.user, digest_enabled=True, digest_time=time(10))
        with patch("modules.v2.tg_notifier.delivery.build_personal_digest", return_value="сводка"):
            with patch("modules.v2.tg_notifier.delivery.send_message", side_effect=TelegramNotifierError("502")):
                self.assertEqual(delivery.send_due_digests(now=at_msk(10, 5)), 0)
            self.assertEqual(delivery.send_due_digests(now=at_msk(10, 10)), 1)

    def test_urgent_notices_go_silently_at_night(self):
        TelegramUserPrefs.objects.create(user=self.user)
        self.assertEqual(delivery.deliver_notice(self.user, "сделка", urgent=True, now=at_msk(3)), delivery.SENT)
        self.assertTrue(self.sent()[-1]["disable_notification"])

    def test_daily_cap(self):
        TelegramUserPrefs.objects.create(user=self.user, quiet_enabled=False, daily_limit=2)
        results = [delivery.deliver_notice(self.user, f"n{i}", now=at_msk(12)) for i in range(4)]
        self.assertEqual(results, [delivery.SENT, delivery.SENT, delivery.LIMITED, delivery.LIMITED])
        texts = [payload["text"] for payload in self.sent()]
        self.assertEqual(len(texts), 3)  # two notices and one «cap reached» note
        self.assertIn("дневной лимит", texts[-1])
        self.assertEqual(delivery.deliver_notice(self.user, "завтра", now=at_msk(12) + timedelta(days=1)), delivery.SENT)

    def test_market_notifications_are_copied_to_telegram(self):
        from modules.v2.notifications.services import notify

        TelegramUserPrefs.objects.create(user=self.user, quiet_enabled=False)
        with self.captureOnCommitCallbacks(execute=True):
            notify(self.user, "market_deal", object_id=1, payload={"event": "new", "code": "D-1", "name": "AK", "price": "1000.00", "deadline": "30.10 12:00"}, dedupe=False)
        reply = self.sent()[-1]
        self.assertIn("Новая сделка D-1", reply["text"])
        self.assertEqual(reply["reply_markup"]["inline_keyboard"][0][0]["text"], "Открыть сделку")
        self.assertIn("/market/deal/D-1/", reply["reply_markup"]["inline_keyboard"][0][0]["url"])

    def test_other_notifications_stay_on_the_site(self):
        from modules.v2.notifications.services import notify

        with self.captureOnCommitCallbacks(execute=True):
            notify(self.user, "steam_price_alert", object_id=1, payload={"title": "x"})
        self.assertEqual(self.sent(), [])

    def test_personal_digest(self):
        SteamItemFavorite.objects.create(user=self.user, family_key="AK-47 | Redline", item=self.item)
        self.history(self.item, "1000", timedelta(days=2))
        TelegramUserPrefs.objects.create(user=self.user, digest_enabled=True, digest_time=time(10))
        self.assertEqual(delivery.send_due_digests(now=at_msk(9)), 0)
        with patch("modules.v2.tg_notifier.bot.change_since", return_value=(Decimal("100"), Decimal("10.0"))):
            self.assertEqual(delivery.send_due_digests(now=at_msk(10, 5)), 1)
            self.assertEqual(delivery.send_due_digests(now=at_msk(18)), 0)  # once a day
        self.assertIn("AK-47 | Redline", self.sent()[-1]["text"])

    def test_settings_form_on_the_profile_page(self):
        self.client.force_login(self.user)
        response = self.client.get("/profile/settings/")
        self.assertContains(response, "settings-telegram")
        response = self.client.post("/telegram/bot-settings/", {"quiet_enabled": "on", "quiet_from": "22:00", "quiet_to": "08:00", "daily_limit": "10", "digest_time": "09:30"})
        self.assertEqual(response.status_code, 302)
        prefs = TelegramUserPrefs.objects.get(user=self.user)
        self.assertEqual((prefs.quiet_from, prefs.daily_limit, prefs.digest_enabled), (time(22), 10, False))
