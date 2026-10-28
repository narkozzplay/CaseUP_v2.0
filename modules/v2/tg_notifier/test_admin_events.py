from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.system.services import update_module_states

from .admin_events import build_signup_message, build_topup_message, head_admin_recipients
from .models import TelegramNotifierConfig

User = get_user_model()


def _make_user(username, *, role=UserRole.USER, chat_id=None, superuser=False):
    user = User.objects.create_user(username=username, password="pass12345", is_superuser=superuser, is_staff=superuser)
    UserProfile.objects.filter(user=user).update(role=role, telegram_chat_id=chat_id)
    return User.objects.select_related("profile_v2").get(pk=user.pk)


class AdminEventNoticesTests(TestCase):
    def setUp(self):
        update_module_states({"tg_notifier": True})
        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={"is_enabled": True, "bot_token": "123:abc", "public_base_url": "https://case-up.ru"},
        )
        self.chief = _make_user("chief", role=UserRole.CHIEF_ADMIN, chat_id="200")
        self.admin = _make_user("admin", role=UserRole.ADMIN, chat_id="100")

    def test_recipients_are_the_highest_rank_with_telegram(self):
        _make_user("boss_without_tg", role=UserRole.SUPER_ADMIN)
        self.assertEqual(head_admin_recipients(), [self.chief])

        owner = _make_user("owner", superuser=True, chat_id="300")
        self.assertEqual(head_admin_recipients(), [owner])

    @patch("modules.v2.tg_notifier.services.send_message")
    def test_signup_sends_to_head_admin_after_commit(self, send_message):
        with self.captureOnCommitCallbacks(execute=True):
            newbie = _make_user("newbie")

        send_message.assert_called_once()
        kwargs = send_message.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], "200")
        self.assertIn("Новый пользователь", kwargs["text"])
        self.assertIn(f"newbie (ID {newbie.pk})", kwargs["text"])
        self.assertIn(reverse("control_center:user_detail", args=[newbie.pk]), kwargs["text"])

    @patch("modules.v2.tg_notifier.services.send_message")
    def test_signup_notice_can_be_switched_off(self, send_message):
        TelegramNotifierConfig.objects.update(admin_signup_notices=False)
        with self.captureOnCommitCallbacks(execute=True):
            _make_user("newbie")
        send_message.assert_not_called()

    @patch("modules.v2.tg_notifier.services.send_message")
    def test_topup_sends_amount_and_balance(self, send_message):
        payer = _make_user("payer")
        with self.captureOnCommitCallbacks(execute=True):
            adjust_balance(user=payer, delta=Decimal("1500.00"), reason=LedgerReason.TOPUP)
            adjust_balance(user=payer, delta=Decimal("-100.00"), reason=LedgerReason.CASE_OPEN)

        send_message.assert_called_once()
        text = send_message.call_args.kwargs["text"]
        self.assertIn("Пополнение баланса", text)
        self.assertIn("Сумма: +1 500.00 ₽", text)
        self.assertIn("Баланс после: 1 500.00 ₽", text)

    @patch("modules.v2.tg_notifier.services.send_message")
    def test_nothing_sent_when_bot_is_off(self, send_message):
        TelegramNotifierConfig.objects.update(is_enabled=False)
        with self.captureOnCommitCallbacks(execute=True):
            _make_user("newbie")
        send_message.assert_not_called()

    def test_messages_render_without_payment_or_referral(self):
        payer = _make_user("payer")
        entry = adjust_balance(user=payer, delta=Decimal("50.00"), reason=LedgerReason.TOPUP)
        self.assertIn("payer", build_signup_message(payer))
        self.assertIn("+50.00 ₽", build_topup_message(entry))

    @patch("modules.v2.tg_notifier.services.send_message")
    def test_settings_page_saves_toggles_and_sends_sample(self, send_message):
        self.client.force_login(self.chief)
        url = reverse("tg_notifier:settings")
        page = self.client.get(url)
        self.assertContains(page, "Уведомления главному админу")

        self.client.post(url, {"action": "save_settings", "is_enabled": "on", "updates_mode": "polling", "investment_message_template": "x", "admin_signup_notices": "on"})
        config = TelegramNotifierConfig.objects.get()
        self.assertTrue(config.admin_signup_notices)
        self.assertFalse(config.admin_topup_notices)

        self.client.post(url, {"action": "admin_notice_sample"})
        send_message.assert_called_once()
        self.assertIn("Пример уведомления", send_message.call_args.kwargs["text"])
