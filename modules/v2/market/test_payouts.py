from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.system.services import update_module_states

from . import deals, payouts
from .models import Deal, Listing
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

    def test_spent_earnings_are_no_longer_withdrawable(self):
        self.complete(self.sell("300.00", "1"))
        adjust_balance(user=self.seller, delta=Decimal("-250.00"), reason=LedgerReason.CASE_OPEN, amount_snapshot=Decimal("250.00"))
        wallet = payouts.seller_wallet(self.seller)
        self.assertEqual((wallet.available, wallet.spent), (Decimal("50.00"), Decimal("250.00")))
        adjust_balance(user=self.seller, delta=Decimal("-50.00"), reason=LedgerReason.MARKET_PAYOUT, amount_snapshot=Decimal("50.00"))
        wallet = payouts.seller_wallet(self.seller)
        self.assertEqual((wallet.withdrawn, wallet.available), (Decimal("50.00"), Decimal("0.00")))

    def test_tab_is_only_for_sellers(self):
        client = Client()
        client.force_login(self.buyer)
        self.assertRedirects(client.get(reverse("market:payout")), reverse("market:sell"), fetch_redirect_response=False)
        self.assertNotContains(client.get(reverse("market:deals")), reverse("market:payout"))

        deal = self.sell("300.00", "1")
        deals.mark_sent(self.seller, deal.code)
        client.force_login(self.seller)
        page = client.get(reverse("market:payout"))
        self.assertContains(page, "Вывод денег сейчас в разработке")
        self.assertContains(page, deal.code)
        self.assertContains(page, "подтверждение покупателя")
        self.assertContains(client.get(reverse("market:index")), reverse("market:payout"))
