from pathlib import Path

from django.core.files import File
from django.db import migrations

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_banner"
PICKEM_URL = "/major/singapore-2026/"


def attach_media(apps, schema_editor):
    HouseBanner = apps.get_model("ads", "HouseBanner")
    for banner in HouseBanner.objects.filter(url=PICKEM_URL):
        # Whatever the admin already uploaded stays as it is.
        if not banner.image:
            with (SEED_DIR / "pickem.webp").open("rb") as handle:
                banner.image.save("promo_pickem.webp", File(handle), save=True)
        if not banner.video:
            with (SEED_DIR / "pickem_loop.mp4").open("rb") as handle:
                banner.video.save("promo_pickem_loop.mp4", File(handle), save=True)


class Migration(migrations.Migration):

    dependencies = [
        ("majors", "0005_singapore_banner"),
        ("ads", "0006_telegram_invite_link"),
    ]

    operations = [
        migrations.RunPython(attach_media, migrations.RunPython.noop),
    ]
