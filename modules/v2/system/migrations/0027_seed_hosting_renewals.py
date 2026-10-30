"""Reg.ru services renewed by date, as of 30.10.2026: the shared hosting with the relay and the SSL certificate."""

from datetime import date
from decimal import Decimal

from django.db import migrations

ITEMS = (
    {
        "name": "Хостинг «Хост-А» u3683277",
        "kind": "hosting",
        "plan": "relay.case-up.ru (ретранслятор Steam и Telegram, бэкапы)",
        "price_rub": Decimal("388"),
        "period": "month",
        "paid_until": date(2026, 11, 14),
        "note": "Рег.ру → Хостинги → «Продлить»",
    },
    {
        "name": "SSL DomainSSL case-up.ru",
        "kind": "other",
        "plan": "Бесплатный сертификат Рег.ру",
        "price_rub": Decimal("0"),
        "period": "year",
        "paid_until": date(2026, 12, 28),
        "note": "Автопродление 13.12.2026: нужна привязанная карта или деньги на балансе Рег.ру",
    },
)


def seed(apps, schema_editor):
    HostingItem = apps.get_model("system_config", "HostingItem")
    for item in ITEMS:
        if not HostingItem.objects.filter(name=item["name"]).exists():
            HostingItem.objects.create(billing="renewal", **item)


class Migration(migrations.Migration):
    dependencies = [("system_config", "0026_seed_hosting")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
