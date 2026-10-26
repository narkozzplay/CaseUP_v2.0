from django.db import migrations
from django.utils import timezone

# Categories without a rule were never refreshed at all. Missing rules are added; existing ones stay as they are.
CATEGORIES = ("skin", "case", "sticker", "graffiti", "charm", "other")


def setup(apps, schema_editor):
    Rule = apps.get_model("celery_tasks", "PriceAutoUpdateRule")
    now = timezone.now()
    for category in CATEGORIES:
        Rule.objects.get_or_create(
            category=category,
            defaults={"schedule_mode": "interval", "interval_minutes": 5, "is_enabled": True, "next_run_at": now},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("celery_tasks", "0005_price_queue_rules"),
    ]

    operations = [migrations.RunPython(setup, migrations.RunPython.noop)]
