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


def _yookassa_response(*, status="pending", payment_id="2d8f-yk", amount="300.00", status_code=200):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = {
        "id": payment_id,
        "status": status,
        "paid": status == "succeeded",
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/checkout/payments/v2/contract?orderId=2d8f-yk"},
    }
    return response


@override_settings(DEBUG=True)
class DevProviderFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payer", password="pass12345")
        self.client.login(username="payer", password="pass12345")

    def _start(self, amount="300"):
        response = self.client.post(reverse("payments:topup"), {"amount": amount})
        payment = Payment.objects.get(user=self.user)
        return response, payment

    def test_topup_page_renders_with_presets(self):
        response = self.client.get(reverse("payments:topup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пополнение баланса")
        self.assertContains(response, 'name="amount" value="500"')

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
        response = self.client.post(reverse("payments:topup"), {"amount": "300"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())


@override_settings(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
class YooKassaFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="yk_payer", password="pass12345")
        self.client.login(username="yk_payer", password="pass12345")

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_topup_redirects_to_yookassa_checkout(self, request_mock):
        request_mock.return_value = _yookassa_response()

        response = self.client.post(reverse("payments:topup"), {"amount": "300"})

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
        self.client.post(reverse("payments:topup"), {"amount": "300"})

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
        self.client.post(reverse("payments:topup"), {"amount": "300"})

        # Body claims success, but the API still says pending.
        body = {"event": "payment.succeeded", "object": {"id": "2d8f-yk", "status": "succeeded"}}
        self.client.post(reverse("payments_public:webhook", args=["yookassa"]), data=json.dumps(body), content_type="application/json")

        self.assertFalse(LedgerEntry.objects.filter(user=self.user).exists())

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_amount_mismatch_is_not_credited(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300"})
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

        response = self.client.post(reverse("payments:topup"), {"amount": "300"}, follow=True)

        self.assertContains(response, "Authentication by given credentials failed")
        self.assertEqual(Payment.objects.get(user=self.user).status, Payment.Status.FAILED)

    @mock.patch("modules.v2.payments.providers.yookassa.requests.request")
    def test_webhook_returns_503_when_api_unreachable(self, request_mock):
        request_mock.return_value = _yookassa_response()
        self.client.post(reverse("payments:topup"), {"amount": "300"})

        request_mock.side_effect = PaymentProviderError("down")
        response = self.client.post(
            reverse("payments_public:webhook", args=["yookassa"]),
            data=json.dumps({"object": {"id": "2d8f-yk"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
