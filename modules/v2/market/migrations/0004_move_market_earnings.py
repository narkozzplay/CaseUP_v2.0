from decimal import Decimal

from django.db import migrations

ZERO = Decimal("0.00")
COMMENT = "Разделение баланса: заработанное на рынке переведено на рыночный баланс"


def forwards(apps, schema_editor):
    """Market sales used to land on the single balance. Move what each seller earned and has not spent
    to the new market balance.

    The seller's history is replayed in order. Sales add to the market share and payouts take from it;
    after every entry the share is capped at the balance at that moment, so money spent on the site counts
    as spent sale money and a later top-up never becomes withdrawable."""
    LedgerEntry = apps.get_model("profiles", "LedgerEntry")
    UserProfile = apps.get_model("profiles", "UserProfile")

    seller_ids = LedgerEntry.objects.filter(reason="market_sale").values_list("user_id", flat=True).distinct()
    for user_id in seller_ids:
        profile = UserProfile.objects.select_for_update().filter(user_id=user_id).first()
        if profile is None:
            continue
        share = ZERO
        for reason, delta, balance_after in LedgerEntry.objects.filter(user_id=user_id).order_by("created_at", "id").values_list("reason", "delta", "balance_after"):
            if reason in ("market_sale", "market_payout", "market_payout_cancel"):
                share += delta
            share = max(min(share, balance_after), ZERO)
        amount = min(share, profile.balance_coins).quantize(Decimal("0.01"))
        if amount <= ZERO:
            continue
        profile.balance_coins -= amount
        profile.market_balance_coins += amount
        profile.save(update_fields=["balance_coins", "market_balance_coins"])
        key = f"market:split:{user_id}"
        LedgerEntry.objects.create(user_id=user_id, wallet="game", delta=-amount, balance_after=profile.balance_coins, reason="market_transfer", amount_snapshot=amount, idempotency_key=f"{key}:out", comment=COMMENT)
        LedgerEntry.objects.create(user_id=user_id, wallet="market", delta=amount, balance_after=profile.market_balance_coins, reason="market_transfer", amount_snapshot=amount, idempotency_key=f"{key}:in", comment=COMMENT)


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0003_payoutrequest"),
        ("profiles", "0023_market_balance"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
