from django.db import migrations
from django.utils import timezone

# The price queue refreshes a small batch per run, so the rules run often: every 5 minutes.
QUEUE_CATEGORIES = ("skin", "case", "sticker")


def setup(apps, schema_editor):
    Rule = apps.get_model("celery_tasks", "PriceAutoUpdateRule")
    now = timezone.now()
    for category in QUEUE_CATEGORIES:
        rule, _ = Rule.objects.get_or_create(category=category, defaults={"schedule_mode": "interval", "interval_minutes": 5})
        rule.schedule_mode = "interval"
        rule.interval_minutes = 5
        rule.is_enabled = True
        rule.next_run_at = now
        rule.save(update_fields=["schedule_mode", "interval_minutes", "is_enabled", "next_run_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("celery_tasks", "0004_rename_celery_task_is_enab_2c6ce0_idx_celery_task_is_enab_85c386_idx_and_more"),
        ("steam_catalog", "0011_steamitem_price_checked_at"),
    ]

    operations = [migrations.RunPython(setup, migrations.RunPython.noop)]
