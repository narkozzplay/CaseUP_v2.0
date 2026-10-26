from pathlib import Path

from django.core.files import File
from django.db import migrations
from django.db.models import Q

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_videos"
# Promo banners from 0002 by their link → the looped animation shipped with the code.
PROMO_VIDEOS = {
    "/donate/": "donate_loop.mp4",
    "https://t.me/caseup_pro_anons": "telegram_loop.mp4",
    "/subscriptions/": "no_ads_loop.mp4",
}


def attach_videos(apps, schema_editor):
    HouseBanner = apps.get_model("ads", "HouseBanner")
    for url, filename in PROMO_VIDEOS.items():
        path = SEED_DIR / filename
        if not path.exists():
            continue
        # The video needs a picture as its poster; a video the admin already uploaded stays as it is.
        banners = HouseBanner.objects.filter(url=url).exclude(Q(image__isnull=True) | Q(image="")).filter(Q(video__isnull=True) | Q(video=""))
        for banner in banners:
            with path.open("rb") as handle:
                banner.video.save(f"promo_{filename}", File(handle), save=True)


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0004_house_banner_video"),
    ]

    operations = [
        migrations.RunPython(attach_videos, migrations.RunPython.noop),
    ]
