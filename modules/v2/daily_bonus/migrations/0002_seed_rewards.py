from decimal import Decimal

from django.db import migrations

DEFAULT_AMOUNTS = {1: "2", 2: "3", 3: "4", 4: "5", 5: "7", 6: "10"}
DAY7_FALLBACK = "15"


def seed(apps, schema_editor):
    Reward = apps.get_model("daily_bonus", "DailyBonusReward")
    Settings = apps.get_model("daily_bonus", "DailyBonusSettings")
    SimCase = apps.get_model("case_simulator", "SimCase")
    Settings.objects.get_or_create(pk=1)
    for day, amount in DEFAULT_AMOUNTS.items():
        Reward.objects.get_or_create(day=day, defaults={"kind": "balance", "amount": Decimal(amount)})
    # Day 7: the cheapest paid case on the site; until one exists it pays the fallback amount.
    cheapest = None
    for case in SimCase.objects.filter(is_active=True).exclude(kind="terminal"):
        price = (case.case_price or 0) + (case.key_price or 0)
        if price > 0 and (cheapest is None or price < cheapest[0]):
            cheapest = (price, case)
    Reward.objects.get_or_create(
        day=7,
        defaults={"kind": "case", "amount": Decimal(DAY7_FALLBACK), "case": cheapest[1] if cheapest else None},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("daily_bonus", "0001_initial"),
        ("case_simulator", "0003_simopening_sold"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
