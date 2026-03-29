from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0006_rename_steam_catal_item_id_9c2f8d_idx_steam_catal_item_id_6d3c16_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceDropAlertRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item_type", models.CharField(choices=[("skin", "Skin"), ("case", "Case"), ("sticker", "Sticker"), ("graffiti", "Graffiti"), ("charm", "Charm"), ("other", "Other")], db_index=True, max_length=16, unique=True)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("lookback_hours", models.PositiveSmallIntegerField(default=4)),
                ("min_drop_rub", models.DecimalField(decimal_places=2, default=Decimal("200.00"), max_digits=12)),
                ("min_drop_percent", models.DecimalField(decimal_places=2, default=Decimal("10.00"), max_digits=7)),
                ("cooldown_minutes", models.PositiveIntegerField(default=360)),
                ("notify_transport", models.CharField(choices=[("telegram", "Telegram"), ("internal", "Internal")], db_index=True, default="telegram", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["item_type"],
                "verbose_name": "Price drop alert rule",
                "verbose_name_plural": "Price drop alert rules",
            },
        ),
        migrations.CreateModel(
            name="PriceDropAlertEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notify_transport", models.CharField(choices=[("telegram", "Telegram"), ("internal", "Internal")], db_index=True, default="telegram", max_length=16)),
                ("trigger_source", models.CharField(blank=True, db_index=True, default="celery", max_length=16)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("ignored", "Ignored"), ("error", "Error")], db_index=True, default="pending", max_length=16)),
                ("window_started_at", models.DateTimeField()),
                ("peak_at", models.DateTimeField()),
                ("current_at", models.DateTimeField()),
                ("peak_price_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("current_price_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("drop_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("drop_percent", models.DecimalField(decimal_places=2, max_digits=7)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="price_drop_alert_events", to="steam_catalog.steamitem")),
                ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="steam_catalog.pricedropalertrule")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "verbose_name": "Price drop alert event",
                "verbose_name_plural": "Price drop alert events",
            },
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["status", "created_at"], name="steam_catal_status_97a61f_idx"),
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["item", "created_at"], name="steam_catal_item_id_1d0b26_idx"),
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["rule", "created_at"], name="steam_catal_rule_id_0a4b50_idx"),
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["notify_transport", "status"], name="steam_catal_notify__aa9afc_idx"),
        ),
    ]