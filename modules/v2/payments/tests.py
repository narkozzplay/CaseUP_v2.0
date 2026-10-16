import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from modules.v2.profiles.models import LedgerEntry, LedgerReason

from .models import Payment
from .providers import PaymentProviderError, get_provider
from .providers.yookassa import YooKassaProvider
from .services import sync_payment


User = get_user_model()


def _set_fee(percent: str) -> None:
    from modules.v2.system.services import get_site_configuration

    config = get_site_configuration()
    config.topup_fee_percent = Decimal(percent)
    config.save(update_fields=["topup_fee_percent"])


def _yookassa_response(*, status="pending", payment_id="2d8f-yk", amount="300.00", currency="RUB", test=True, status_code=200):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = {
        "id": payment_id,
        "status": status,
        "paid": status == "succeeded",
        "test": test,
        "amount": {"value": amount, "currency": currency},
        "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/checkout/payments/v2/contract?orderId=2d8f-yk"},
    }
    return response


@override_settings(DEBUG=True)
class DevProviderFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payer", password="pass12345")
        self.client.login(username="payer", password="pass12345")

    def _start(self, amount="300"):
        response = self.client.post(reverse("payments:topup"), {"amount": amount, "accept_terms": "on"})
        payment = Payment.objects.get(user=self.user)
        return response, payment

    def test_topup_page_renders_with_presets(self):
        response = self.client.get(reverse("payments:topup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пополнение баланса")
        self.assertContains(response, 'name="preset" value="500"')

    def test_successful_dev_payment_credits_balance_once(self):
        response, payment = self._start()
        self.assertRedirects(response, reverse("payments:dev_checkout", args=[payment.public_id]), fetch_redirect_response=False)
        self.assertEqual(payment.status, Payment.Status.PENDING)

        response = self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "pay"}, follow=True)
        self.assertContains(response, "Баланс пополнен на 300.00")

        # Coming back to the return URL again must not credit twice.
        self.client.get(reverse("payments:return", args=[payment.public_id]))

        payment.refresh_from_db()
        self.user.profile_v2.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(self.user.profile_v2.balance_coins, Decimal("300.00"))
        entry = LedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.reason, LedgerReason.TOPUP)
        self.assertEqual(payment.ledger_entry_id, entry.id)

    def test_canceled_dev_payment_does_not_credit(self):
        _response, payment = self._start()
        self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "cancel"}, follow=True)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELED)
        self.assertFalse(LedgerEntry.objects.filter(user=self.user).exists())

    def test_amount_outside_limits_is_rejected(self):
        response = self.client.post(reverse("payments:topup"), {"amount": "10"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_user_cannot_open_foreign_payment(self):
        _response, payment = self._start()
        User.objects.create_user(username="intruder", password="pass12345")
        self.client.login(username="intruder", password="pass12345")

        response = self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "pay"})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(LedgerEntry.objects.exists())

    @override_settings(DEBUG=False)
    def test_dev_provider_is_disabled_without_debug(self):
        self.assertIsNone(get_provider())
        response = self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())


@override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
class YooKassaFlowTests(TestCase):
    def setUp(self):
        _set_fee("0")  # these tests check the provider flow, the commission has its own tests
        self.user = User.objects.create_user(username="yk_payer", password="pass12345")
        self.client.login(username="yk_payer", password="pass12345")

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_topup_redirects_to_yookassa_checkout(self, request_mock):
        request_mock.return_value = _yookassa_response()

        response = self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        self.assertRedirects(response, "https://yoomoney.ru/checkout/payments/v2/contract?orderId=2d8f-yk", fetch_redirect_response=False)
        payment = Payment.objects.get(user=self.user)
        self.assertEqual(payment.provider, "yookassa")
        self.assertEqual(payment.external_id, "2d8f-yk")
        _method, url = request_mock.call_args.args
        self.assertTrue(url.endswith("/payments"))
        kwargs = request_mock.call_args.kwargs
        self.assertEqual(kwargs["auth"], ("123456", "test_secret"))
        self.assertEqual(kwargs["headers"]["Idempotence-Key"], str(payment.public_id))
        self.assertEqual(kwargs["json"]["amount"], {"value": "300.00", "currency": "RUB"})
        self.assertTrue(kwargs["json"]["capture"])
        self.assertTrue(YooKassaProvider().is_test_mode)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_webhook_credits_after_verifying_with_api(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        request_mock.return_value = _yookassa_response(status="succeeded")
        body = {"type": "notification", "event": "payment.succeeded", "object": {"id": "2d8f-yk", "status": "succeeded"}}
        for _ in range(2):
            response = self.client.post(
                reverse("payments_public:webhook", args=["yookassa"]),
                data=json.dumps(body),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(request_mock.call_args.args, ("GET", "https://api.yookassa.ru/v3/payments/2d8f-yk"))
        self.user.profile_v2.refresh_from_db()
        self.assertEqual(self.user.profile_v2.balance_coins, Decimal("300.00"))
        self.assertEqual(LedgerEntry.objects.filter(user=self.user).count(), 1)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_forged_webhook_does_not_credit(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        # Body claims success, but the API still says pending.
        body = {"event": "payment.succeeded", "object": {"id": "2d8f-yk", "status": "succeeded"}}
        self.client.post(reverse("payments_public:webhook", args=["yookassa"]), data=json.dumps(body), content_type="application/json")

        self.assertFalse(LedgerEntry.objects.filter(user=self.user).exists())

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_amount_mismatch_is_not_credited(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        payment = Payment.objects.get(user=self.user)

        request_mock.return_value = _yookassa_response(status="succeeded", amount="3.00")
        payment = sync_payment(payment)

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertFalse(LedgerEntry.objects.filter(user=self.user).exists())

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_provider_error_is_shown_and_payment_marked_failed(self, request_mock):
        failed = mock.Mock(status_code=401)
        failed.json.return_value = {"type": "error", "code": "invalid_credentials", "description": "Authentication by given credentials failed"}
        request_mock.return_value = failed

        response = self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"}, follow=True)

        self.assertContains(response, "Authentication by given credentials failed")
        self.assertEqual(Payment.objects.get(user=self.user).status, Payment.Status.FAILED)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_webhook_returns_503_when_api_unreachable(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        request_mock.side_effect = PaymentProviderError("down")
        response = self.client.post(
            reverse("payments_public:webhook", args=["yookassa"]),
            data=json.dumps({"object": {"id": "2d8f-yk"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_currency_mismatch_is_not_credited(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        payment = Payment.objects.get(user=self.user)

        request_mock.return_value = _yookassa_response(status="succeeded", currency="USD")
        payment = sync_payment(payment)

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertFalse(LedgerEntry.objects.filter(user=self.user).exists())

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_test_mode_payment_is_flagged(self, request_mock):
        request_mock.return_value = _yookassa_response(test=True)
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        self.assertTrue(Payment.objects.get(user=self.user).is_test)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_credit_sends_site_notification(self, request_mock):
        from modules.v2.notifications.models import Notification
        from modules.v2.notifications.services import render_notification

        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        payment = Payment.objects.get(user=self.user)

        request_mock.return_value = _yookassa_response(status="succeeded")
        with self.captureOnCommitCallbacks(execute=True):
            sync_payment(payment)
        with self.captureOnCommitCallbacks(execute=True):
            sync_payment(payment)  # already credited: no second notification

        notifications = Notification.objects.filter(recipient=self.user, kind="balance_topup_succeeded")
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(render_notification(notifications.get(), "ru").title, "Баланс пополнен на 300.00 ₽")


@override_settings(DEBUG=True, PAYMENTS_MAX_OPEN_PER_USER=2)
class TopupRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="spammer", password="pass12345")
        self.client.login(username="spammer", password="pass12345")

    def test_too_many_open_payments_are_refused(self):
        for _ in range(2):
            self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        response = self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"}, follow=True)

        self.assertContains(response, "Слишком много неоплаченных платежей")
        self.assertEqual(Payment.objects.filter(user=self.user).count(), 2)

    def test_old_open_payments_do_not_count(self):
        from datetime import timedelta

        from django.utils import timezone

        for _ in range(2):
            self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})
        Payment.objects.update(created_at=timezone.now() - timedelta(minutes=20))
        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        self.assertEqual(Payment.objects.filter(user=self.user).count(), 3)


@override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
class ReconcilePendingPaymentsTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        self.now = timezone.now()
        self.user = User.objects.create_user(username="reconcile", password="pass12345")

    def _payment(self, *, minutes_ago, external_id="2d8f-yk", status=Payment.Status.PENDING):
        from datetime import timedelta

        payment = Payment.objects.create(
            user=self.user, provider="yookassa", amount=Decimal("300.00"), status=status, external_id=external_id
        )
        Payment.objects.filter(pk=payment.pk).update(created_at=self.now - timedelta(minutes=minutes_ago))
        return payment

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_lost_webhook_payment_is_credited(self, request_mock):
        from .services import reconcile_pending_payments

        payment = self._payment(minutes_ago=10)
        request_mock.return_value = _yookassa_response(status="succeeded")

        stats = reconcile_pending_payments(now=self.now)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(stats["credited"], 1)
        self.user.profile_v2.refresh_from_db()
        self.assertEqual(self.user.profile_v2.balance_coins, Decimal("300.00"))

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_fresh_and_ancient_payments_are_left_alone(self, request_mock):
        from .services import reconcile_pending_payments

        self._payment(minutes_ago=1, external_id="fresh")
        self._payment(minutes_ago=60 * 24 * 4, external_id="ancient")

        stats = reconcile_pending_payments(now=self.now)

        request_mock.assert_not_called()
        self.assertEqual(stats["checked"], 0)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_canceled_payment_is_closed_and_errors_do_not_stop_the_batch(self, request_mock):
        from .services import reconcile_pending_payments

        first = self._payment(minutes_ago=30, external_id="broken")
        second = self._payment(minutes_ago=20, external_id="canceled-one")
        request_mock.side_effect = [PaymentProviderError("down"), _yookassa_response(status="canceled", payment_id="canceled-one")]

        stats = reconcile_pending_payments(now=self.now)

        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["closed"], 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.PENDING)
        self.assertEqual(second.status, Payment.Status.CANCELED)

    def test_unstarted_payment_is_closed_after_an_hour(self):
        from .services import reconcile_pending_payments

        stuck = self._payment(minutes_ago=90, external_id="", status=Payment.Status.CREATED)
        recent = self._payment(minutes_ago=5, external_id="", status=Payment.Status.CREATED)

        reconcile_pending_payments(now=self.now)

        stuck.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual(stuck.status, Payment.Status.FAILED)
        self.assertEqual(recent.status, Payment.Status.CREATED)

    def test_celery_task_is_registered_on_beat(self):
        from config.celery import app

        self.assertEqual(app.conf.beat_schedule["reconcile-pending-payments"]["task"], "payments.reconcile_pending_payments")


class PaymentDeployCheckTests(TestCase):
    def test_test_keys_in_production_warn(self):
        from .checks import check_yookassa_live_keys

        with override_settings(DEBUG=False, YOOKASSA_SECRET_KEY="test_abc"):
            self.assertEqual([w.id for w in check_yookassa_live_keys(None)], ["caseup.W002"])
        with override_settings(DEBUG=False, YOOKASSA_SECRET_KEY="live_abc"):
            self.assertEqual(check_yookassa_live_keys(None), [])


@override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret", PAYMENTS_WEBHOOK_IP_CHECK="on", PAYMENTS_WEBHOOK_PROXY_COUNT=1)
class WebhookSourceIpTests(TestCase):
    url = "/payments/webhook/yookassa/"

    def _post(self, **meta):
        body = json.dumps({"event": "payment.succeeded", "object": {"id": "unknown-id"}})
        return self.client.post(self.url, data=body, content_type="application/json", **meta)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_foreign_address_is_rejected_before_any_api_call(self, request_mock):
        response = self._post(HTTP_X_FORWARDED_FOR="203.0.113.7")

        self.assertEqual(response.status_code, 403)
        request_mock.assert_not_called()

    def test_yookassa_address_behind_nginx_is_accepted(self):
        # Nginx appends the real peer; whatever the client put on the left is ignored.
        self.assertEqual(self._post(HTTP_X_FORWARDED_FOR="203.0.113.7, 185.71.76.5").status_code, 200)
        self.assertEqual(self._post(HTTP_X_FORWARDED_FOR="2a02:5180::17").status_code, 200)

    def test_spoofed_left_entry_does_not_pass(self):
        self.assertEqual(self._post(HTTP_X_FORWARDED_FOR="185.71.76.5, 203.0.113.7").status_code, 403)

    @override_settings(PAYMENTS_WEBHOOK_PROXY_COUNT=0)
    def test_without_proxy_remote_addr_is_used(self):
        self.assertEqual(self._post(REMOTE_ADDR="77.75.156.11", HTTP_X_FORWARDED_FOR="203.0.113.7").status_code, 200)
        self.assertEqual(self._post(REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="185.71.76.5").status_code, 403)

    @override_settings(PAYMENTS_WEBHOOK_IP_CHECK="auto", DEBUG=True)
    def test_auto_mode_is_off_in_development(self):
        self.assertEqual(self._post(HTTP_X_FORWARDED_FOR="203.0.113.7").status_code, 200)

    @override_settings(PAYMENTS_WEBHOOK_IP_CHECK="auto", DEBUG=False)
    def test_auto_mode_is_on_in_production(self):
        self.assertEqual(self._post(HTTP_X_FORWARDED_FOR="203.0.113.7").status_code, 403)


class CheckYooKassaCommandTests(TestCase):
    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("check_yookassa", stdout=out)
        return out.getvalue()

    @override_settings(YOOKASSA_SHOP_ID="", YOOKASSA_SECRET_KEY="")
    def test_missing_keys_explain_where_to_get_them(self):
        from django.core.management.base import CommandError

        with self.assertRaisesMessage(CommandError, "Ключи API"):
            self._run()

    @override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret", SITE_BASE_URL="http://127.0.0.1:8000")
    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_valid_test_keys_print_mode_and_webhook_url(self, request_mock):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"account_id": "123456", "test": True, "status": "enabled", "payment_methods": ["bank_card", "sbp"]}
        request_mock.return_value = response

        output = self._run()

        self.assertEqual(request_mock.call_args.args, ("GET", "https://api.yookassa.ru/v3/me"))
        self.assertIn("Ключи ЮKassa работают", output)
        self.assertIn("ТЕСТОВЫЙ", output)
        self.assertIn("http://127.0.0.1:8000/payments/webhook/yookassa/", output)
        self.assertIn("SITE_BASE_URL локальный", output)

    @override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_wrong")
    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_rejected_keys_fail_with_reason(self, request_mock):
        from django.core.management.base import CommandError

        response = mock.Mock(status_code=401)
        response.json.return_value = {"type": "error", "code": "invalid_credentials", "description": "Authentication by given credentials failed"}
        request_mock.return_value = response

        with self.assertRaisesMessage(CommandError, "Authentication by given credentials failed"):
            self._run()


@override_settings(DEBUG=True)
class TopupAgreementConsentTests(TestCase):
    def setUp(self):
        _set_fee("0")  # these tests check the provider flow, the commission has its own tests
        self.user = User.objects.create_user(username="consent", password="pass12345")
        self.client.login(username="consent", password="pass12345")

    def test_payment_is_not_created_without_consent(self):
        response = self.client.post(reverse("payments:topup"), {"amount": "300"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "примите условия Пользовательского соглашения")
        self.assertFalse(Payment.objects.exists())

    def test_accepted_agreement_version_is_stored_on_the_payment(self):
        from frontend.legal import AGREEMENT_VERSION

        self.client.post(reverse("payments:topup"), {"amount": "300", "accept_terms": "on"})

        self.assertEqual(Payment.objects.get(user=self.user).terms_version, AGREEMENT_VERSION)

    def test_preset_button_uses_its_amount_and_still_needs_consent(self):
        self.client.post(reverse("payments:topup"), {"preset": "500", "amount": ""})
        self.assertFalse(Payment.objects.exists())

        self.client.post(reverse("payments:topup"), {"preset": "500", "amount": "", "accept_terms": "on"})
        self.assertEqual(Payment.objects.get(user=self.user).amount, Decimal("500.00"))

    def test_empty_amount_is_rejected(self):
        response = self.client.post(reverse("payments:topup"), {"amount": "", "accept_terms": "on"})

        self.assertContains(response, "Укажите сумму пополнения")
        self.assertFalse(Payment.objects.exists())

    def test_topup_page_links_the_agreement(self):
        response = self.client.get(reverse("payments:topup"))

        self.assertContains(response, 'name="accept_terms"')
        self.assertContains(response, reverse("frontend:agreement"))


@override_settings(DEBUG=True)
class TopupCommissionTests(TestCase):
    def setUp(self):
        _set_fee("8.5")
        self.user = User.objects.create_user(username="fee_payer", password="pass12345")
        self.client.login(username="fee_payer", password="pass12345")

    def test_dev_payment_puts_the_amount_on_the_balance(self):
        from modules.v2.profiles.models import UserProfile

        page = self.client.get(reverse("payments:topup"))
        self.assertContains(page, "к оплате 542.50 ₽")  # the 500 preset
        self.client.post(reverse("payments:topup"), {"amount": "100", "accept_terms": "on"})
        payment = Payment.objects.get(user=self.user)
        self.assertEqual((payment.amount, payment.credit_amount, payment.fee_amount), (Decimal("108.50"), Decimal("100.00"), Decimal("8.50")))
        response = self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "pay"}, follow=True)
        self.assertContains(response, "Баланс пополнен на 100.00")
        self.assertEqual(UserProfile.objects.get(user=self.user).balance_coins, Decimal("100.00"))

    @override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="live_secret")
    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_live_payment_books_the_fee_and_the_provider_cost(self, request_mock):
        from modules.v2.business.models import RevenueEntry, RevenueSource

        request_mock.return_value = _yookassa_response(amount="108.50", test=False)
        self.client.post(reverse("payments:topup"), {"amount": "100", "accept_terms": "on"})
        succeeded = _yookassa_response(status="succeeded", amount="108.50", test=False)
        succeeded.json.return_value["income_amount"] = {"value": "104.70", "currency": "RUB"}
        request_mock.return_value = succeeded
        body = {"event": "payment.succeeded", "object": {"id": "2d8f-yk", "status": "succeeded"}}
        self.client.post(reverse("payments_public:webhook", args=["yookassa"]), data=json.dumps(body), content_type="application/json")

        payment = Payment.objects.get(user=self.user)
        self.assertEqual((payment.status, payment.provider_fee), (Payment.Status.SUCCEEDED, Decimal("3.80")))
        self.assertEqual(LedgerEntry.objects.get(user=self.user).delta, Decimal("100.00"))
        self.assertEqual(RevenueEntry.objects.get(source=RevenueSource.TOPUP_FEE).amount, Decimal("8.50"))

    def test_return_after_payment_goes_back_to_the_market_lot(self):
        self.client.post(reverse("payments:topup") + "?next=/market/lot/7/?buy=1", {"amount": "100", "accept_terms": "on", "next": "/market/lot/7/?buy=1"})
        payment = Payment.objects.get(user=self.user)
        response = self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "pay"})
        follow = self.client.get(response["Location"])
        self.assertRedirects(follow, "/market/lot/7/?buy=1", fetch_redirect_response=False)

    def test_foreign_return_address_is_ignored(self):
        self.client.post(reverse("payments:topup"), {"amount": "100", "accept_terms": "on", "next": "https://evil.example/"})
        payment = Payment.objects.get(user=self.user)
        response = self.client.post(reverse("payments:dev_checkout", args=[payment.public_id]), {"outcome": "pay"})
        follow = self.client.get(response["Location"])
        self.assertRedirects(follow, reverse("payments:topup"), fetch_redirect_response=False)
