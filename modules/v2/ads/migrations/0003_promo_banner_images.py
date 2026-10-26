from pathlib import Path

from django.core.files import File
from django.db import migrations

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_images"
# Promo banners from 0002 by their link → the illustration shipped with the code.
PROMO_IMAGES = {
    "/donate/": "donate.webp",
    "https://t.me/caseup_pro_anons": "telegram.webp",
    "/subscriptions/": "no_ads.webp",
}


def attach_images(apps, schema_editor):
    HouseBanner = apps.get_model("ads", "HouseBanner")
    for url, filename in PROMO_IMAGES.items():
        path = SEED_DIR / filename
        if not path.exists():
            continue
        # A picture the admin already uploaded stays as it is.
        for banner in HouseBanner.objects.filter(url=url).filter(image__isnull=True) | HouseBanner.objects.filter(url=url, image=""):
            with path.open("rb") as handle:
                banner.image.save(f"promo_{filename}", File(handle), save=True)


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0002_house_banners"),
    ]

    operations = [
        migrations.RunPython(attach_images, migrations.RunPython.noop),
    ]
