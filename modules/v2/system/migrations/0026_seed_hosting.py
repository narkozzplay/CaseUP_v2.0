"""The production server as of 30.10.2026 (Рег.облако panel): tariff, floating IP and the balance."""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.db import migrations


def seed(apps, schema_editor):
    HostingSettings = apps.get_model("system_config", "HostingSettings")
    HostingItem = apps.get_model("system_config", "HostingItem")
    if HostingItem.objects.exists() or HostingSettings.objects.exists():
        return
    HostingSettings.objects.create(
        pk=1,
        provider_name="Рег.облако",
        panel_url="https://cloud.reg.ru/panel/servers/8143817/information",
        balance_rub=Decimal("728.85"),
        balance_at=datetime(2026, 10, 30, 10, 14, tzinfo=dt_timezone.utc),
    )
    HostingItem.objects.create(
        name="CaseUP_prod", kind="server", billing="balance", plan="Stdp C2-M2-D40 · 2 vCPU, 2 ГБ RAM, 40 ГБ, Москва-2",
        price_rub=Decimal("1.76"), period="hour",
    )
    HostingItem.objects.create(
        name="Плавающий IP 185.46.11.244", kind="ip", billing="balance", plan="IPv4",
        price_rub=Decimal("187.38"), period="month",
    )


class Migration(migrations.Migration):
    dependencies = [("system_config", "0025_server_monitor_hosting")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
