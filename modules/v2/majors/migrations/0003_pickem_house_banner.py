"""CaseUP's own banner inviting to the Singapore Pick'em, shown until the final.

It starts as text only; the illustration (and an animated loop) is uploaded in Control Center → «Реклама».
"""
from datetime import datetime, timezone as dt_timezone

from django.db import migrations

URL = "/major/singapore-2026/"


def add_banner(apps, schema_editor):
    HouseBanner = apps.get_model("ads", "HouseBanner")
    if HouseBanner.objects.filter(url=URL).exists():
        return
    HouseBanner.objects.create(
        name="Pick'em PGL Major Singapore 2026",
        title="Pick'em мейджора в Сингапуре",
        text="Угадай команды на 3-0, финалистов и чемпиона. Участие бесплатное, за верные прогнозы — призы.",
        url=URL,
        button="Сделать прогноз",
        audience="all",
        # Like the other promos, paid ad-free accounts do not see it.
        show_to_ad_free=False,
        weight=30,
        # 13 December, 23:59 Moscow time: the day of the grand final.
        ends_at=datetime(2026, 12, 13, 20, 59, tzinfo=dt_timezone.utc),
        is_enabled=True,
    )


def remove_banner(apps, schema_editor):
    apps.get_model("ads", "HouseBanner").objects.filter(url=URL).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("majors", "0002_seed_singapore"),
        ("ads", "0006_telegram_invite_link"),
    ]

    operations = [migrations.RunPython(add_banner, remove_banner)]
