from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole, Wallet
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.system.services import update_module_states

from . import deals, payouts
from .models import Deal, Listing, PayoutRequest
from .notifications import KIND_PAYOUT
from .tests import make_seller


class PayoutWalletTests(TestCase):
    def setUp(self):
        cache.clear()
        update_module_states({"market": True})
        self.seller = make_seller("pay_seller")
        self.buyer = make_seller("pay_buyer")
        adjust_balance(user=self.buyer, delta=Decimal("1000.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("1000.00"))

    def sell(self, price: str, asset: str) -> Deal:
        listing = Listing.objects.create(seller=self.seller, asset_id=asset, market_hash_name=f"Recoil Case {asset}", price=Decimal(price))
        return deals.buy(self.buyer, listing_id=listing.pk)

    def complete(self, deal: Deal):
        deals.mark_sent(self.seller, deal.code)
        deals.confirm_received(self.buyer, deal.code)
        deals.process_deals(now=timezone.now() + timedelta(days=8))

    def test_wallet_counts_only_market_money(self):
        adjust_balance(user=self.seller, delta=Decimal("500.00"), reason=LedgerReason.TOPUP, amount_snapshot=Decimal("500.00"))
        done = self.sell("300.00", "1")
        self.complete(done)
        self.sell("120.00", "2")  # still open
        wallet = payouts.seller_wallet(self.seller)
        self.assertEqual((wallet.earned, wallet.held, wallet.available), (Decimal("300.00"), Decimal("120.00"), Decimal("300.00")))

    def test_sale_goes_to_the_market_balance(self):
        self.complete(self.sell("300.00", "1"))
        profile = UserProfile.objects.get(user=self.seller)
        self.assertEqual((profile.balance_coins, profile.market_balance_coins), (Decimal("0.00"), Decimal("300.00")))
        entry = LedgerEntry.objects.get(user=self.seller, reason=LedgerReason.MARKET_SALE)
        self.assertEqual((entry.wallet, entry.balance_after), (Wallet.MARKET, Decimal("300.00")))

    def test_market_money_cannot_pay_for_cases(self):
        self.complete(self.sell("300.00", "1"))
        with self.assertRaises(BalanceError):
            adjust_balance(user=self.seller, delta=Decimal("-50.00"), reason=LedgerReason.CASE_OPEN, amount_snapshot=Decimal("50.00"))

    def test_transfer_to_game_balance_is_one_way(self):
        self.complete(self.sell("300.00", "1"))
        payouts.transfer_to_game(self.seller, "100")
        profile = UserProfile.objects.get(user=self.seller)
        self.assertEqual((profile.balance_coins, profile.market_balance_coins), (Decimal("100.00"), Decimal("200.00")))
        with self.assertRaisesMessage(payouts.PayoutError, "недостаточно"):
            payouts.transfer_to_game(self.seller, "500")
        self.assertEqual(payouts.seller_wallet(self.seller).available, Decimal("200.00"))

    def request(self, amount="250", **extra):
        data = {"amount": amount, "method": PayoutRequest.Method.SBP, "requisites": "+7 900 123-45-67, Т-Банк", "recipient_name": "Иван И."}
        data.update(extra)
        return payouts.request_payout(self.seller, **data)

    def test_request_debits_the_market_balance_and_keeps_requisites_encrypted(self):
        self.complete(self.sell("300.00", "1"))
        payout = self.request("250")
        self.assertRegex(payout.code, r"^PAY-[A-Z2-9]{6}$")
        wallet = payouts.seller_wallet(self.seller)
        self.assertEqual((wallet.available, wallet.pending), (Decimal("50.00"), Decimal("250.00")))
        with connection.cursor() as cursor:
            cursor.execute("SELECT requisites FROM market_payoutrequest WHERE id = %s", [payout.pk])
            self.assertNotIn("900", cursor.fetchone()[0])
        self.assertEqual(PayoutRequest.objects.get(pk=payout.pk).requisites, "+7 900 123-45-67, Т-Банк")
        with self.assertRaisesMessage(payouts.PayoutError, "Предыдущая заявка"):
            self.request("100")

    def test_request_checks_amount_and_requisites(self):
        self.complete(self.sell("300.00", "1"))
        with self.assertRaisesMessage(payouts.PayoutError, "Минимальная сумма"):
            self.request("50")
        with self.assertRaisesMessage(payouts.PayoutError, "недостаточно"):
            self.request("301")
        with self.assertRaisesMessage(payouts.PayoutError, "Номер карты"):
            self.request("200", method=PayoutRequest.Method.CARD, requisites="1234")
        self.assertFalse(PayoutRequest.objects.exists())
        self.assertEqual(payouts.seller_wallet(self.seller).available, Decimal("300.00"))

    def test_cancel_and_reject_return_the_amount(self):
        self.complete(self.sell("300.00", "1"))
        payout = self.request("200")
        payouts.cancel_payout(self.seller, payout.code)
        self.assertEqual(payouts.seller_wallet(self.seller).available, Decimal("300.00"))

        admin = make_seller("pay_admin")
        payout = self.request("200")
        with self.assertRaisesMessage(payouts.PayoutError, "причину"):
            payouts.process_payout(admin, payout.code, paid=False)
        payouts.process_payout(admin, payout.code, paid=False, note="Реквизиты не на владельца")
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRequest.Status.REJECTED)
        self.assertEqual(payouts.seller_wallet(self.seller).available, Decimal("300.00"))
        with self.assertRaisesMessage(payouts.PayoutError, "уже обработана"):
            payouts.process_payout(admin, payout.code, paid=True)

    def test_admin_marks_paid_from_control_center(self):
        self.complete(self.sell("300.00", "1"))
        payout = self.request("300")
        admin = make_seller("pay_admin_cc")
        profile = UserProfile.objects.get(user=admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        client = Client()
        client.force_login(admin)
        page = client.get(reverse("control_center:market"))
        self.assertContains(page, payout.code)
        self.assertContains(page, "+7 900 123-45-67")
        with self.captureOnCommitCallbacks(execute=True):
            client.post(reverse("control_center:market_payout", args=[payout.code]), {"decision": "paid", "note": "СБП"})
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRequest.Status.PAID)
        wallet = payouts.seller_wallet(self.seller)
        self.assertEqual((wallet.withdrawn, wallet.available, wallet.pending), (Decimal("300.00"), Decimal("0.00"), Decimal("0.00")))
        self.assertTrue(Notification.objects.filter(recipient=self.seller, kind=KIND_PAYOUT).exists())

    def test_tab_is_only_for_sellers(self):
        client = Client()
        client.force_login(self.buyer)
        self.assertRedirects(client.get(reverse("market:payout")), reverse("market:sell"), fetch_redirect_response=False)
        self.assertNotContains(client.get(reverse("market:deals")), reverse("market:payout"))

        deal = self.sell("300.00", "1")
        deals.mark_sent(self.seller, deal.code)
        client.force_login(self.seller)
        page = client.get(reverse("market:payout"))
        self.assertContains(page, "Минимальная сумма вывода")
        self.assertContains(page, deal.code)
        self.assertContains(page, "подтверждение покупателя")
        self.assertContains(client.get(reverse("market:index")), reverse("market:payout"))

    def test_payout_form_through_the_page(self):
        self.complete(self.sell("300.00", "1"))
        client = Client()
        client.force_login(self.seller)
        self.assertContains(client.get(reverse("market:payout")), reverse("market:payout_request"))
        client.post(reverse("market:payout_request"), {"amount": "120", "method": "card", "requisites": "2200 1234 5678 9012", "recipient_name": "Иван И."})
        payout = PayoutRequest.objects.get(user=self.seller)
        self.assertEqual((payout.amount, payout.requisites, payout.masked_requisites), (Decimal("120.00"), "2200123456789012", "•••• 9012"))
        self.assertContains(client.get(reverse("market:payout")), "•••• 9012")
        client.post(reverse("market:payout_cancel", args=[payout.code]))
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRequest.Status.CANCELLED)
