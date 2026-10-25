from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory

from .channel_digest import TELEGRAM_TEXT_LIMIT, build_digest_text, visible_length, collect_price_moves, due_slot, post_due_digest
from .forms import TelegramChannelDigestForm
from .models import TelegramNotifierConfig
from .services import TelegramNotifierError

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 25, 20, 3, tzinfo=MSK)


class ChannelDigestTests(TestCase):
    def setUp(self):
        self.config = TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            public_base_url="https://case-up.ru",
            channel_digest_enabled=True,
            channel_chat_id="@caseup_pro_anons",
        )

    def _item(self, name, *, before, after, family="", checked_hours_ago=1, changed_hours_ago=3, baseline_hours_ago=30):
        item = SteamItem.objects.create(
            market_hash_name=name,
            family_name=family or name,
            price_rub=after,
            is_active=True,
            price_checked_at=NOW - timedelta(hours=checked_hours_ago),
        )
        old = SteamItemPriceHistory.objects.create(item=item, price_rub=before, source="test")
        new = SteamItemPriceHistory.objects.create(item=item, price_rub=after, source="test")
        SteamItemPriceHistory.objects.filter(pk=old.pk).update(created_at=NOW - timedelta(hours=baseline_hours_ago))
        SteamItemPriceHistory.objects.filter(pk=new.pk).update(created_at=NOW - timedelta(hours=changed_hours_ago))
        return item

    def test_picks_risers_and_fallers_by_percent(self):
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))
        self._item("AWP | Asiimov (Field-Tested)", before=Decimal("5000"), after=Decimal("5100"))
        self._item("Revolution Case", before=Decimal("100"), after=Decimal("80"))

        digest = collect_price_moves(config=self.config, now=NOW)

        self.assertEqual([m.item.market_hash_name for m in digest.risers], ["AK-47 | Redline (Field-Tested)", "AWP | Asiimov (Field-Tested)"])
        self.assertEqual(digest.risers[0].change_percent, Decimal("20.0"))
        self.assertEqual([m.change_percent for m in digest.fallers], [Decimal("-20.0")])

    def test_skips_stale_cheap_and_ancient_baselines(self):
        self._item("Stale", before=Decimal("100"), after=Decimal("200"), checked_hours_ago=20)
        self._item("Cheap", before=Decimal("2"), after=Decimal("9"))
        self._item("Ancient baseline", before=Decimal("100"), after=Decimal("300"), baseline_hours_ago=24 * 30)
        self._item("Old move", before=Decimal("100"), after=Decimal("300"), changed_hours_ago=30, baseline_hours_ago=40)

        self.assertTrue(collect_price_moves(config=self.config, now=NOW).is_empty)
        self.assertIsNone(build_digest_text(self.config, now=NOW))

    def test_one_line_per_family(self):
        self._item("AK-47 | Redline (Field-Tested)", family="AK-47 | Redline", before=Decimal("1000"), after=Decimal("1300"))
        self._item("AK-47 | Redline (Minimal Wear)", family="AK-47 | Redline", before=Decimal("2000"), after=Decimal("2400"))

        digest = collect_price_moves(config=self.config, now=NOW)

        self.assertEqual([m.item.market_hash_name for m in digest.risers], ["AK-47 | Redline (Field-Tested)"])

    def test_text_links_to_public_item_pages(self):
        item = self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        text = build_digest_text(self.config, now=NOW)

        self.assertIn(f'href="https://case-up.ru{item.get_absolute_url()}?utm_source=telegram', text)
        self.assertIn("1 000 ₽ → <b>1 200 ₽</b> (+20.0%)", text)
        self.assertIn("#CaseUP #CounterStrike2", text)
        self.assertNotIn("Подешевели", text)

    def test_long_digest_is_trimmed_to_one_message(self):
        TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(channel_digest_top_n=10)
        self.config.refresh_from_db()
        for index in range(10):
            self._item(f"Sticker {index} " + "x" * 230, before=Decimal("100"), after=Decimal(110 + index))
            self._item(f"Capsule {index} " + "y" * 230, before=Decimal("100"), after=Decimal(90 - index))

        text = build_digest_text(self.config, now=NOW)

        self.assertLessEqual(visible_length(text), TELEGRAM_TEXT_LIMIT)
        self.assertIn("Подорожали", text)
        self.assertIn("Подешевели", text)

    def test_due_slot_once_per_time_and_not_too_late(self):
        self.config.channel_digest_times = "10:00, 20:00"
        self.assertEqual(due_slot(self.config, now=NOW), datetime(2026, 10, 25, 20, 0, tzinfo=MSK))
        self.assertIsNone(due_slot(self.config, now=datetime(2026, 10, 25, 19, 59, tzinfo=MSK)))
        self.assertIsNone(due_slot(self.config, now=datetime(2026, 10, 25, 23, 30, tzinfo=MSK)))
        self.config.channel_digest_last_sent_at = NOW
        self.assertIsNone(due_slot(self.config, now=NOW + timedelta(minutes=5)))

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_beat_posts_once_per_slot(self, send_mock):
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        self.assertEqual(post_due_digest(now=NOW), "posted")
        self.assertEqual(post_due_digest(now=NOW + timedelta(minutes=5)), "not_due")

        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "@caseup_pro_anons")
        self.assertEqual(send_mock.call_args.kwargs["parse_mode"], "HTML")

    @patch("modules.v2.tg_notifier.channel_digest.send_message", side_effect=TelegramNotifierError("Telegram API timeout."))
    def test_telegram_failure_is_retried_next_tick(self, _send_mock):
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        self.assertEqual(post_due_digest(now=NOW), "error")

        self.config.refresh_from_db()
        self.assertIsNone(self.config.channel_digest_last_sent_at)
        self.assertIn("timeout", self.config.channel_digest_last_error)

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_quiet_day_is_not_retried(self, send_mock):
        self.assertEqual(post_due_digest(now=NOW), "empty")
        self.assertEqual(post_due_digest(now=NOW + timedelta(minutes=5)), "not_due")
        send_mock.assert_not_called()

    def test_disabled_digest_does_nothing(self):
        TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(channel_digest_enabled=False)
        self.assertEqual(post_due_digest(now=NOW), "disabled")

    def test_form_normalizes_channel_and_times(self):
        form = TelegramChannelDigestForm(
            {
                "channel_digest_enabled": "on",
                "channel_chat_id": "https://t.me/caseup_pro_anons",
                "channel_digest_times": "20:00; 9:30",
                "channel_digest_top_n": 5,
                "channel_digest_min_price_rub": "10",
                "channel_digest_fresh_hours": 12,
            },
            instance=self.config,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["channel_chat_id"], "@caseup_pro_anons")
        self.assertEqual(form.cleaned_data["channel_digest_times"], "09:30, 20:00")

        bad = TelegramChannelDigestForm({**form.data, "channel_digest_times": "вечером"}, instance=self.config)
        self.assertFalse(bad.is_valid())

    def test_admin_sees_preview(self):
        user = get_user_model().objects.create_user(username="admin_digest", password="StrongPass123")
        UserProfile.objects.filter(user=user).update(role=UserRole.ADMIN)
        self.client.login(username="admin_digest", password="StrongPass123")

        response = self.client.post(reverse("tg_notifier:settings"), {"action": "preview_channel", **self._form_data()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Автопостинг в канал")
        self.assertContains(response, "нет заметных изменений")

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_publish_now_uses_the_channel_on_screen(self, send_mock):
        user = get_user_model().objects.create_user(username="admin_publish", password="StrongPass123")
        UserProfile.objects.filter(user=user).update(role=UserRole.ADMIN)
        self.client.login(username="admin_publish", password="StrongPass123")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"), checked_hours_ago=0, changed_hours_ago=0)

        with patch("modules.v2.tg_notifier.channel_digest.timezone.now", return_value=NOW):
            self.client.post(
                reverse("tg_notifier:settings"),
                {"action": "post_channel_now", **self._form_data(channel_chat_id="@new_channel")},
            )

        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "@new_channel")
        self.config.refresh_from_db()
        self.assertEqual(self.config.channel_chat_id, "@new_channel")

    def _form_data(self, **overrides):
        return {
            "channel_digest_enabled": "on",
            "channel_chat_id": "@caseup_pro_anons",
            "channel_digest_times": "20:00",
            "channel_digest_top_n": 5,
            "channel_digest_min_price_rub": "10",
            "channel_digest_fresh_hours": 12,
            **overrides,
        }
