from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.system.services import update_module_states
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.steam_trading.models import SteamPurchase
from modules.v2.steam_catalog.models import PriceDropAlertEvent, PriceDropAlertRule

from .models import TelegramLinkToken, TelegramNotifierConfig
from .services import (
    TelegramNotifierError,
    bind_profile_by_telegram_start,
    build_connect_url_for_user,
    build_investment_message_context,
    extract_start_token_from_message_text,
    render_investment_message,
    unlink_user_telegram,
    build_webhook_url,
)

User = get_user_model()


class TgNotifierTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _grant_plan(self, user, level: int):
        plan = SubscriptionPlan.objects.get(level=level)
        return UserSubscription.objects.create(user=user, plan=plan)

    def _make_user(self, username: str, role: str):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = role
        profile.save(update_fields=["role"])
        return user

    def _make_purchase(self, *, user):
        item = SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            name_ru="Кейс «Революция»",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("36.83"),
            is_active=True,
        )
        return SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 7, 8, 0)),
            quantity=1,
            purchase_price_rub=Decimal("1.00"),
            created_by=user,
        )

    def test_plain_user_cannot_open_tg_notifier_page(self):
        self._make_user("plain_tg", UserRole.USER)
        self.client.login(username="plain_tg", password="StrongPass123")

        response = self.client.get(reverse("tg_notifier:settings"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_open_tg_notifier_page(self):
        self._make_user("admin_tg", UserRole.ADMIN)
        self.client.login(username="admin_tg", password="StrongPass123")

        response = self.client.get(reverse("tg_notifier:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TG Notifier")

    def test_admin_can_save_tg_notifier_config(self):
        self._make_user("admin_save_tg", UserRole.ADMIN)
        self.client.login(username="admin_save_tg", password="StrongPass123")

        response = self.client.post(
            reverse("tg_notifier:settings"),
            {
                "action": "save_settings",
                "is_enabled": "on",
                "bot_token": "123456:ABCDEF",
                "bot_username": "caseup_bot",
                "default_chat_id": "-100555",
                "public_base_url": "https://abc123.ngrok-free.dev",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )
        self.assertEqual(response.status_code, 302)

        config = TelegramNotifierConfig.objects.get(singleton_key=1)
        self.assertTrue(config.is_enabled)
        self.assertEqual(config.bot_token, "123456:ABCDEF")
        self.assertEqual(config.bot_username, "caseup_bot")
        self.assertEqual(config.default_chat_id, "-100555")
        self.assertEqual(config.public_base_url, "https://abc123.ngrok-free.dev")

    @patch("modules.v2.tg_notifier.views.send_message")
    def test_test_send_uses_sync_service(self, send_message_mock):
        send_message_mock.return_value.chat_id = "-100555"

        self._make_user("admin_test_send", UserRole.ADMIN)
        self.client.login(username="admin_test_send", password="StrongPass123")

        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={
                "is_enabled": True,
                "bot_token": "123456:ABCDEF",
                "bot_username": "caseup_bot",
                "default_chat_id": "-100555",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )

        response = self.client.post(
            reverse("tg_notifier:settings"),
            {
                "action": "test_send",
                "chat_id": "",
                "text": "hello tg",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once_with(text="hello tg", chat_id=None)
        self.assertContains(response, "Тестовое сообщение отправлено")

    @patch("modules.v2.tg_notifier.views.send_message")
    def test_test_send_shows_error_message(self, send_message_mock):
        send_message_mock.side_effect = TelegramNotifierError("bad token")

        self._make_user("admin_test_send_error", UserRole.ADMIN)
        self.client.login(username="admin_test_send_error", password="StrongPass123")

        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="wrong",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template="Предмет: {{ item_name }}",
        )

        response = self.client.post(
            reverse("tg_notifier:settings"),
            {
                "action": "test_send",
                "chat_id": "",
                "text": "hello tg",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тестовая отправка не удалась")

    def test_render_investment_message_contains_expected_values(self):
        owner = self._make_user("farmer_render_tg", UserRole.FARMER)
        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template=(
                "{{ trade_lock_label }} | {{ item_name }} | {{ current_value_after_fee }} | "
                "{{ profit_abs }} | {{ hold_status_label }}"
            ),
        )
        purchase = self._make_purchase(user=owner)

        message = render_investment_message(purchase=purchase)

        self.assertIn("Кейс «Революция»", message)
        self.assertIn("31.31 ₽", message)
        self.assertIn("+35.83 ₽", message)
        self.assertIn("готовы к продаже", message)

    def test_build_investment_message_context_uses_active_remaining_position(self):
        owner = self._make_user("farmer_ctx_tg", UserRole.FARMER)
        purchase = self._make_purchase(user=owner)

        context = build_investment_message_context(purchase)

        self.assertEqual(context["quantity"], 1)
        self.assertEqual(context["unit_buy_price"], "1.00 ₽")
        self.assertEqual(context["current_price"], "36.83 ₽")
        self.assertEqual(context["current_value_after_fee"], "31.31 ₽")

    def test_build_connect_url_creates_reusable_active_token(self):
        user = self._make_user("tg_link_user", UserRole.FARMER)
        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={
                "is_enabled": True,
                "bot_token": "123456:ABCDEF",
                "bot_username": "caseup_bot",
                "default_chat_id": "-100555",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )

        url_1 = build_connect_url_for_user(user)
        url_2 = build_connect_url_for_user(user)

        self.assertEqual(url_1, url_2)
        self.assertIn("https://t.me/caseup_bot?start=caseup_", url_1)
        self.assertEqual(TelegramLinkToken.objects.filter(user=user).count(), 1)

    def test_extract_start_token_from_message_text(self):
        token = extract_start_token_from_message_text("/start caseup_abcdef123456")
        self.assertEqual(token, "abcdef123456")

    def test_bind_profile_by_telegram_start_links_profile(self):
        user = self._make_user("tg_bind_user", UserRole.FARMER)
        link_token = TelegramLinkToken.objects.create(user=user)

        result = bind_profile_by_telegram_start(
            start_token=link_token.token,
            chat_id="7151390596",
            telegram_username="narkozzplay",
            telegram_first_name="Fedor",
        )

        profile = UserProfile.objects.get(user=user)
        self.assertEqual(result.chat_id, "7151390596")
        self.assertEqual(profile.telegram_chat_id, "7151390596")
        self.assertEqual(profile.telegram_username, "narkozzplay")
        self.assertEqual(profile.telegram_first_name, "Fedor")
        self.assertIsNotNone(profile.telegram_linked_at)

        link_token.refresh_from_db()
        self.assertIsNotNone(link_token.used_at)

    def test_unlink_user_telegram_clears_profile_fields(self):
        user = self._make_user("tg_unlink_user", UserRole.FARMER)
        profile = UserProfile.objects.get(user=user)
        profile.telegram_chat_id = "7151390596"
        profile.telegram_username = "narkozzplay"
        profile.telegram_first_name = "Fedor"
        profile.telegram_linked_at = timezone.now()
        profile.telegram_last_interaction_at = timezone.now()
        profile.save()

        unlink_user_telegram(user)
        profile.refresh_from_db()

        self.assertIsNone(profile.telegram_chat_id)
        self.assertEqual(profile.telegram_username, "")
        self.assertEqual(profile.telegram_first_name, "")
        self.assertIsNone(profile.telegram_linked_at)

    @patch("modules.v2.tg_notifier.views.send_message")
    def test_webhook_binds_profile_by_start_message(self, send_message_mock):
        user = self._make_user("tg_webhook_user", UserRole.FARMER)
        config = TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template="Предмет: {{ item_name }}",
            webhook_secret="supersecret",
        )
        link_token = TelegramLinkToken.objects.create(user=user)

        response = self.client.post(
            reverse("tg_notifier_public:webhook", args=[config.webhook_secret]),
            data={
                "message": {
                    "chat": {"id": 7151390596, "type": "private"},
                    "from": {"id": 111, "username": "narkozzplay", "first_name": "Fedor"},
                    "text": f"/start caseup_{link_token.token}",
                }
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.telegram_chat_id, "7151390596")
        send_message_mock.assert_called_once()


    @patch("modules.v2.tg_notifier.services._telegram_api_request")
    def test_send_message_passes_parse_mode(self, api_request_mock):
        api_request_mock.return_value = {"ok": True, "result": {"message_id": 1}}
        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template="Предмет: {{ item_name }}",
        )

        from .services import send_message

        send_message(text="<b>hello</b>", parse_mode="HTML")

        payload = api_request_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["parse_mode"], "HTML")

    def test_build_webhook_url_prefers_config_public_base_url(self):
        config = TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            public_base_url="https://abc123.ngrok-free.dev",
            webhook_secret="supersecret",
            investment_message_template="Предмет: {{ item_name }}",
        )
    
        webhook_url = build_webhook_url(config)
        self.assertEqual(
            webhook_url,
            "https://abc123.ngrok-free.dev/telegram/webhook/supersecret/",
        )

    def test_user_can_open_notification_center(self):
        user = self._make_user("tg_center_user", UserRole.USER)
        self._grant_plan(user, 3)
        update_module_states({"tg_notifier": True})
        profile = UserProfile.objects.get(user=user)
        profile.telegram_chat_id = "7151390596"
        profile.telegram_username = "narkozzplay"
        profile.save(update_fields=["telegram_chat_id", "telegram_username"])
    
        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={
                "is_enabled": True,
                "bot_token": "123456:ABCDEF",
                "bot_username": "caseup_bot",
                "default_chat_id": "-100555",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )
    
        PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.DOWN,
            lookback_hours=4,
            min_drop_rub=Decimal("200.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("50.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=360,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )
    
        self.client.login(username="tg_center_user", password="StrongPass123")
        response = self.client.get(reverse("tg_notifier:center"))
    
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Центр уведомлений")
        self.assertContains(response, "Разблокировка Steam-покупок")
        self.assertContains(response, "Price movement alerts")
        self.assertContains(response, "Подключен")
    def test_notification_center_locked_without_level_3_subscription(self):
        self._make_user("tg_center_locked", UserRole.USER)
        update_module_states({"tg_notifier": True})

        self.client.login(username="tg_center_locked", password="StrongPass123")
        response = self.client.get(reverse("tg_notifier:center"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Центр уведомлений закрыт тарифом", status_code=403)

    def test_notification_center_respects_global_module_flag(self):
        user = self._make_user("tg_center_flagged", UserRole.USER)
        self._grant_plan(user, 3)
        update_module_states({"tg_notifier": True})
        update_module_states({"tg_notifier": False})

        self.client.login(username="tg_center_flagged", password="StrongPass123")
        response = self.client.get(reverse("tg_notifier:center"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Центр уведомлений закрыт тарифом", status_code=403)
