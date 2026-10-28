from pathlib import Path

from django.core.files import File
from django.db import migrations

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_banner"
# Field → file shipped with the code (the picture is the poster of the looped animation).
BANNER_FILES = {
    "banner": "singapore-2026.webp",
    "banner_video_mp4": "singapore-2026.mp4",
    "banner_video_webm": "singapore-2026.webm",
}


def attach_banner(apps, schema_editor):
    Major = apps.get_model("majors", "Major")
    major = Major.objects.filter(slug="singapore-2026").first()
    if major is None:
        return
    for field, filename in BANNER_FILES.items():
        path = SEED_DIR / filename
        # Whatever the admin already uploaded stays as it is.
        if not path.exists() or getattr(major, field):
            continue
        with path.open("rb") as handle:
            getattr(major, field).save(filename, File(handle), save=True)


class Migration(migrations.Migration):

    dependencies = [
        ("majors", "0004_major_banner_video"),
    ]

    operations = [
        migrations.RunPython(attach_banner, migrations.RunPython.noop),
    ]
