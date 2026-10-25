from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory

from .channel_digest import TELEGRAM_CAPTION_LIMIT, TELEGRAM_TEXT_LIMIT, build_digest_text, post_digest, collect_digest, due_slot, post_due_digest, visible_length
from .forms import TelegramChannelDigestForm
from .models import ChannelDigestPost, ChannelDigestSection, TelegramNotifierConfig
from .digest_image import DigestImageError
from .services import TelegramNotifierError, _multipart, send_message

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 25, 20, 3, tzinfo=MSK)
UP, DOWN = ChannelDigestSection.Direction.UP, ChannelDigestSection.Direction.DOWN


class DigestFixtures(TestCase):
    def setUp(self):
        self.config = TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            public_base_url="https://case-up.ru",
            channel_digest_enabled=True,
            channel_chat_id="@caseup_pro_anons",
            channel_digest_with_image=False,
        )
        # The migration seeds the default sections; each test sets up its own.
        ChannelDigestSection.objects.all().delete()

    def _section(self, title, category="all", direction=UP, **fields):
        return ChannelDigestSection.objects.create(title=title, category=category, direction=direction, **fields)

    def _item(self, name, *, before, after, family="", weapon="", item_type=SteamItem.ItemType.OTHER, checked_hours_ago=1, changed_hours_ago=3, baseline_hours_ago=30):
        item = SteamItem.objects.create(
            market_hash_name=name,
            family_name=family or name,
            item_type=item_type,
            weapon=weapon,
            price_rub=after,
            is_active=True,
            price_checked_at=NOW - timedelta(hours=checked_hours_ago),
        )
        old = SteamItemPriceHistory.objects.create(item=item, price_rub=before, source="test")
        new = SteamItemPriceHistory.objects.create(item=item, price_rub=after, source="test")
        SteamItemPriceHistory.objects.filter(pk=old.pk).update(created_at=NOW - timedelta(hours=baseline_hours_ago))
        SteamItemPriceHistory.objects.filter(pk=new.pk).update(created_at=NOW - timedelta(hours=changed_hours_ago))
        return item


class ChannelDigestTests(DigestFixtures):
    def _names(self, block):
        return [move.item.market_hash_name for move in block.moves]

    def test_sections_pick_by_category_and_direction(self):
        self._section("Кейсы: рост", "type:case", UP, position=1)
        self._section("Ножи: спад", "group:knife", DOWN, position=2)
        self._section("Всё: рост", "all", UP, position=3)
        self._item("Revolution Case", item_type="case", before=Decimal("100"), after=Decimal("120"))
        self._item("Kilowatt Case", item_type="case", before=Decimal("100"), after=Decimal("90"))
        self._item("★ Karambit | Doppler (Factory New)", item_type="skin", before=Decimal("90000"), after=Decimal("81000"))
        self._item("★ Sport Gloves | Vice (Field-Tested)", item_type="skin", weapon="Sport Gloves", before=Decimal("90000"), after=Decimal("70000"))
        self._item("AK-47 | Redline (Field-Tested)", item_type="skin", before=Decimal("1000"), after=Decimal("1500"))

        cases, knives, everything = collect_digest(config=self.config, now=NOW).blocks

        self.assertEqual(self._names(cases), ["Revolution Case"])
        self.assertEqual(self._names(knives), ["★ Karambit | Doppler (Factory New)"])
        self.assertEqual(knives.moves[0].change_percent, Decimal("-10.0"))
        self.assertEqual(self._names(everything), ["AK-47 | Redline (Field-Tested)", "Revolution Case"])

    def test_top_size_and_price_floor_per_section(self):
        self._section("Рост", top_n=1, min_price_rub=Decimal("500"))
        self._item("Cheap", before=Decimal("100"), after=Decimal("300"))
        self._item("Big", before=Decimal("1000"), after=Decimal("1100"))
        self._item("Bigger", before=Decimal("1000"), after=Decimal("1200"))

        (block,) = collect_digest(config=self.config, now=NOW).blocks

        self.assertEqual(self._names(block), ["Bigger"])

    def test_disabled_sections_are_left_out(self):
        self._section("Выключена", is_enabled=False)
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        self.assertEqual(collect_digest(config=self.config, now=NOW).blocks, [])
        self.assertIsNone(build_digest_text(self.config, now=NOW))

    def test_skips_stale_cheap_and_ancient_baselines(self):
        self._section("Рост")
        self._item("Stale", before=Decimal("100"), after=Decimal("200"), checked_hours_ago=20)
        self._item("Cheap", before=Decimal("2"), after=Decimal("9"))
        self._item("Ancient baseline", before=Decimal("100"), after=Decimal("300"), baseline_hours_ago=24 * 30)
        self._item("Old move", before=Decimal("100"), after=Decimal("300"), changed_hours_ago=30, baseline_hours_ago=40)

        self.assertTrue(collect_digest(config=self.config, now=NOW).is_empty)

    def test_one_line_per_family(self):
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", family="AK-47 | Redline", before=Decimal("1000"), after=Decimal("1300"))
        self._item("AK-47 | Redline (Minimal Wear)", family="AK-47 | Redline", before=Decimal("2000"), after=Decimal("2400"))

        (block,) = collect_digest(config=self.config, now=NOW).blocks

        self.assertEqual(self._names(block), ["AK-47 | Redline (Field-Tested)"])

    def test_text_links_items_and_section_to_catalog(self):
        self._section("Кейсы: рост", "type:case")
        self._section("Кейсы: спад", "type:case", DOWN)
        item = self._item("Revolution Case", item_type="case", before=Decimal("1000"), after=Decimal("1200"))

        text = build_digest_text(self.config, now=NOW)

        self.assertIn(f'href="https://case-up.ru{item.get_absolute_url()}?utm_source=telegram', text)
        self.assertIn('href="https://case-up.ru/steam-catalog/?type=case&amp;utm_source=telegram', text)
        self.assertIn("1 000 ₽ → <b>1 200 ₽</b> (+20.0%)", text)
        self.assertIn("#CaseUP #CounterStrike2", text)
        self.assertNotIn("Кейсы: спад", text)

    def test_custom_wording_with_placeholders(self):
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))
        self.config.channel_digest_title = "Цены CS2 · {date} {time}"
        self.config.channel_digest_intro = "Что изменилось за {hours} ч. <без тегов> {unknown}"
        self.config.channel_digest_outro = "Подписывайтесь!"
        self.config.channel_digest_link_text = "Все цены на {site}"
        self.config.channel_digest_hashtags = ""

        text = build_digest_text(self.config, now=NOW)

        self.assertTrue(text.startswith("<b>Цены CS2 · 25.10.2026 20:03</b>\n\nЧто изменилось за 12 ч. &lt;без тегов&gt; {unknown}\n\n"))
        self.assertIn("Подписывайтесь!\n\nЦены Steam Community Market", text)
        self.assertRegex(text, r'<a href="https://case-up.ru/steam-catalog/\?utm_source=[^"]+">Все цены на .+</a>$')
        self.assertNotIn("#CaseUP", text)

    def test_empty_link_text_drops_the_link(self):
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))
        self.config.channel_digest_link_text = ""
        self.config.channel_digest_title = ""

        text = build_digest_text(self.config, now=NOW)

        self.assertTrue(text.startswith("<b>📊 Цены CS2 за сутки · 25.10.2026</b>"))
        self.assertNotIn('href="https://case-up.ru/steam-catalog/?utm', text)

    def test_long_digest_is_trimmed_to_one_message(self):
        self._section("Рост", top_n=10)
        self._section("Спад", direction=DOWN, top_n=10)
        for index in range(10):
            self._item(f"Sticker {index} " + "x" * 230, before=Decimal("100"), after=Decimal(110 + index))
            self._item(f"Capsule {index} " + "y" * 230, before=Decimal("100"), after=Decimal(90 - index))

        text = build_digest_text(self.config, now=NOW)

        self.assertLessEqual(visible_length(text), TELEGRAM_TEXT_LIMIT)
        self.assertIn("Рост", text)
        self.assertIn("Спад", text)

    def test_many_single_row_sections_are_dropped_from_the_end(self):
        for index in range(25):
            self._section(f"Рубрика {index}", position=index)
        self._item("Sticker " + "x" * 230, before=Decimal("100"), after=Decimal("150"))

        text = build_digest_text(self.config, now=NOW)

        self.assertLessEqual(visible_length(text), TELEGRAM_TEXT_LIMIT)
        self.assertIn("Рубрика 0", text)
        self.assertNotIn("Рубрика 24", text)

    def test_due_slot_once_per_time_and_not_too_late(self):
        self.config.channel_digest_times = "10:00, 20:00"
        self.assertEqual(due_slot(self.config, now=NOW), datetime(2026, 10, 25, 20, 0, tzinfo=MSK))
        self.assertIsNone(due_slot(self.config, now=datetime(2026, 10, 25, 19, 59, tzinfo=MSK)))
        self.assertIsNone(due_slot(self.config, now=datetime(2026, 10, 25, 23, 30, tzinfo=MSK)))
        self.config.channel_digest_last_sent_at = NOW
        self.assertIsNone(due_slot(self.config, now=NOW + timedelta(minutes=5)))

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_beat_posts_once_per_slot_and_logs_it(self, send_mock):
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        self.assertEqual(post_due_digest(now=NOW), "posted")
        self.assertEqual(post_due_digest(now=NOW + timedelta(minutes=5)), "not_due")

        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "@caseup_pro_anons")
        self.assertEqual(send_mock.call_args.kwargs["parse_mode"], "HTML")
        post = ChannelDigestPost.objects.get()
        self.assertEqual((post.trigger, post.status), ("schedule", "sent"))

    @patch("modules.v2.tg_notifier.channel_digest.send_message", side_effect=TelegramNotifierError("Telegram API timeout."))
    def test_telegram_failure_is_retried_next_tick(self, _send_mock):
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"))

        self.assertEqual(post_due_digest(now=NOW), "error")

        self.config.refresh_from_db()
        self.assertIsNone(self.config.channel_digest_last_sent_at)
        self.assertIn("timeout", ChannelDigestPost.objects.get(status="error").error)

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_quiet_day_is_not_retried(self, send_mock):
        self._section("Рост")
        self.assertEqual(post_due_digest(now=NOW), "empty")
        self.assertEqual(post_due_digest(now=NOW + timedelta(minutes=5)), "not_due")
        send_mock.assert_not_called()

    def test_disabled_digest_does_nothing(self):
        TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(channel_digest_enabled=False)
        self.assertEqual(post_due_digest(now=NOW), "disabled")

    def test_form_normalizes_channel_and_times(self):
        form = TelegramChannelDigestForm(self._schedule_data(channel_chat_id="https://t.me/caseup_pro_anons", channel_digest_times="20:00; 9:30"), instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["channel_chat_id"], "@caseup_pro_anons")
        self.assertEqual(form.cleaned_data["channel_digest_times"], "09:30, 20:00")

        bad = TelegramChannelDigestForm({**form.data, "channel_digest_times": "вечером"}, instance=self.config)
        self.assertFalse(bad.is_valid())

    def _login_admin(self):
        user = get_user_model().objects.create_user(username="admin_digest", password="StrongPass123")
        UserProfile.objects.filter(user=user).update(role=UserRole.ADMIN)
        self.client.login(username="admin_digest", password="StrongPass123")

    def _schedule_data(self, **overrides):
        return {
            "channel_digest_enabled": "on",
            "channel_chat_id": "@caseup_pro_anons",
            "channel_digest_times": "20:00",
            "channel_digest_fresh_hours": 12,
            **overrides,
        }

    def _page_data(self, action, sections=(), new=None, **schedule):
        data = {"action": action, **self._schedule_data(**schedule)}
        rows = list(sections) + ([new] if new else [])
        data.update({
            "sections-TOTAL_FORMS": str(len(rows)),
            "sections-INITIAL_FORMS": str(len(sections)),
            "sections-MIN_NUM_FORMS": "0",
            "sections-MAX_NUM_FORMS": "1000",
        })
        for index, row in enumerate(rows):
            for key, value in row.items():
                data[f"sections-{index}-{key}"] = value
        return data

    def test_autopost_page_adds_a_section(self):
        self._login_admin()
        existing = self._section("Рост")

        response = self.client.post(
            reverse("tg_notifier:autopost"),
            self._page_data(
                "save",
                sections=[{"id": existing.pk, "is_enabled": "on", "title": "Рост", "category": "all", "direction": "up", "top_n": 5, "min_price_rub": "10", "position": 0}],
                new={"is_enabled": "on", "title": "Перчатки: спад", "category": "group:gloves", "direction": "down", "top_n": 3, "min_price_rub": "1000", "position": 5},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Настройки автопостинга сохранены")
        added = ChannelDigestSection.objects.get(title="Перчатки: спад")
        self.assertEqual((added.category, added.direction, added.top_n), ("group:gloves", "down", 3))

    def test_autopost_page_preview_with_nothing_to_post(self):
        self._login_admin()

        response = self.client.post(reverse("tg_notifier:autopost"), self._page_data("preview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "нет заметных изменений")

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    def test_publish_now_uses_the_channel_on_screen(self, send_mock):
        self._login_admin()
        self._section("Рост")
        self._item("AK-47 | Redline (Field-Tested)", before=Decimal("1000"), after=Decimal("1200"), checked_hours_ago=0, changed_hours_ago=0)

        with patch("modules.v2.tg_notifier.channel_digest.timezone.now", return_value=NOW):
            self.client.post(reverse("tg_notifier:autopost"), self._page_data("post_now", channel_chat_id="@new_channel"))

        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "@new_channel")
        self.config.refresh_from_db()
        self.assertEqual(self.config.channel_chat_id, "@new_channel")
        self.assertEqual(ChannelDigestPost.objects.get().trigger, "manual")

    def test_plain_user_cannot_open_autopost_page(self):
        get_user_model().objects.create_user(username="plain_digest", password="StrongPass123")
        self.client.login(username="plain_digest", password="StrongPass123")
        self.assertEqual(self.client.get(reverse("tg_notifier:autopost")).status_code, 403)


class DigestImageTests(DigestFixtures):
    def setUp(self):
        super().setUp()
        TelegramNotifierConfig.objects.filter(pk=self.config.pk).update(channel_digest_with_image=True)
        self.config.refresh_from_db()
        self._section("Рост", top_n=10)
        self._section("Спад", direction=DOWN, top_n=10)
        for index in range(10):
            self._item(f"Sticker | Long Name Number {index} (Holo) | Paris 2023", before=Decimal("100"), after=Decimal(110 + index))
            self._item(f"Capsule | Long Name Number {index} | Antwerp 2022", before=Decimal("100"), after=Decimal(90 - index))

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    @patch("modules.v2.tg_notifier.channel_digest.send_photo")
    @patch("modules.v2.tg_notifier.digest_image.render_digest_image", return_value=b"jpeg")
    def test_photo_with_caption_within_limit(self, _render, photo_mock, text_mock):
        post_digest(self.config, now=NOW)

        caption = photo_mock.call_args.kwargs["caption"]
        self.assertEqual(photo_mock.call_args.kwargs["photo"], b"jpeg")
        self.assertLessEqual(visible_length(caption), TELEGRAM_CAPTION_LIMIT)
        self.assertIn("/steam-catalog/top/", caption)
        self.assertIn("Спад", caption)
        text_mock.assert_not_called()
        self.assertEqual(ChannelDigestPost.objects.get().status, "sent")

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    @patch("modules.v2.tg_notifier.channel_digest.send_photo")
    @patch("modules.v2.tg_notifier.digest_image.render_digest_image", side_effect=DigestImageError("Playwright не установлен"))
    def test_text_goes_out_when_picture_fails(self, _render, photo_mock, text_mock):
        post_digest(self.config, now=NOW)

        photo_mock.assert_not_called()
        self.assertIn("1. ", text_mock.call_args.kwargs["text"])
        post = ChannelDigestPost.objects.get()
        self.assertEqual(post.status, "sent")
        self.assertIn("Playwright", post.error)

    @patch("modules.v2.tg_notifier.channel_digest.send_message")
    @patch("modules.v2.tg_notifier.channel_digest.send_photo", side_effect=TelegramNotifierError("Telegram API timeout."))
    @patch("modules.v2.tg_notifier.digest_image.render_digest_image", return_value=b"jpeg")
    def test_photo_timeout_does_not_post_a_text_copy(self, _render, _photo, text_mock):
        with self.assertRaises(TelegramNotifierError):
            post_digest(self.config, now=NOW)
        text_mock.assert_not_called()

    def test_public_top_page(self):
        with patch("modules.v2.tg_notifier.channel_digest.timezone.now", return_value=NOW):
            response = self.client.get(reverse("price_movers"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Топ цен CS2 за сутки")
        self.assertContains(response, "Sticker | Long Name Number 9 (Holo) | Paris 2023")
        self.assertContains(response, "+19.0%")
        self.assertContains(response, "t.me/caseup_pro_anons")

    def test_card_title_is_editable(self):
        from .digest_image import render_card_html

        self.config.channel_digest_card_title = "Топ за 3 часа"
        with patch("modules.v2.tg_notifier.channel_digest.timezone.now", return_value=NOW):
            page = render_card_html(collect_digest(config=self.config, now=NOW), config=self.config)

        self.assertIn('<h2 class="dg-title">Топ за 3 часа</h2>', page)

    def test_form_keeps_caption_wording_short(self):
        data = {
            "channel_digest_enabled": "on",
            "channel_chat_id": "@caseup_pro_anons",
            "channel_digest_times": "20:00",
            "channel_digest_fresh_hours": 12,
            "channel_digest_with_image": "on",
            "channel_digest_intro": "а" * 400,
            "channel_digest_outro": "б" * 300,
        }
        form = TelegramChannelDigestForm(data, instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn("1024 символа", str(form.errors["channel_digest_intro"]))

        data.pop("channel_digest_with_image")
        self.assertTrue(TelegramChannelDigestForm(data, instance=self.config).is_valid())


class SendRetryTests(TestCase):
    def setUp(self):
        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEF")

    @patch("modules.v2.tg_notifier.services.time.sleep")
    @patch("modules.v2.tg_notifier.services._telegram_api_request")
    def test_relay_unreachable_is_retried(self, request_mock, _sleep):
        request_mock.side_effect = [
            TelegramNotifierError('Telegram API HTTP 502: {"ok":false,"error":"upstream_unreachable"}'),
            {"ok": True, "result": {}},
        ]
        send_message(text="hi", chat_id="@caseup_pro_anons")
        self.assertEqual(request_mock.call_count, 2)

    @patch("modules.v2.tg_notifier.services.time.sleep")
    @patch("modules.v2.tg_notifier.services._telegram_api_request", side_effect=TelegramNotifierError("Telegram API timeout."))
    def test_timeout_is_not_retried(self, request_mock, _sleep):
        with self.assertRaises(TelegramNotifierError):
            send_message(text="hi", chat_id="@caseup_pro_anons")
        self.assertEqual(request_mock.call_count, 1)


class MultipartTests(TestCase):
    def test_fields_and_file(self):
        body, content_type = _multipart({"chat_id": "@c", "caption": "Цены"}, {"photo": ("d.jpg", b"\xff\xd8", "image/jpeg")})
        boundary = content_type.split("boundary=")[1]
        self.assertTrue(content_type.startswith("multipart/form-data"))
        self.assertIn(b'name="caption"\r\n\r\n' + "Цены".encode(), body)
        self.assertIn(b'name="photo"; filename="d.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\r\n', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))
