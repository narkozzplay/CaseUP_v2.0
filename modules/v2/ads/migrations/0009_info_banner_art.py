from pathlib import Path

from django.core.files import File
from django.db import migrations
from django.db.models import Q

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_images" / "info"
# Seeded info banners from 0008 by name → the ChatGPT art shipped with the code.
BANNER_ART = {
    "Статистика CS2": "info_stats.webp",
    "Подключи FACEIT": "info_faceit.webp",
    "Рынок: продать скины": "info_market.webp",
    "Инвестиции в скины": "info_invest.webp",
    "Поддержи CaseUP": "info_donate.webp",
    "Куда идут донаты": "info_donate_where.webp",
    "Кейсы": "info_cases.webp",
    "Оформление профиля": "info_profile.webp",
    "Бонус дня": "info_bonus.webp",
}


def attach_art(apps, schema_editor):
    InfoBanner = apps.get_model("ads", "InfoBanner")
    for name, filename in BANNER_ART.items():
        path = SEED_DIR / filename
        if not path.exists():
            continue
        # A picture the admin already uploaded stays as it is.
        for banner in InfoBanner.objects.filter(name=name).filter(Q(image__isnull=True) | Q(image="")):
            with path.open("rb") as handle:
                banner.image.save(filename, File(handle), save=True)


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0008_seed_info_banners"),
    ]

    operations = [
        migrations.RunPython(attach_art, migrations.RunPython.noop),
    ]
