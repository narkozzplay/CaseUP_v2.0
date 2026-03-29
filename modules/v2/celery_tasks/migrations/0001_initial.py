from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("steam_catalog", "0006_rename_steam_catal_item_id_9c2f8d_idx_steam_catal_item_id_6d3c16_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceAutoUpdateRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("skin", "Skin"),
                            ("case", "Case"),
                            ("sticker", "Sticker"),
                            ("graffiti", "Graffiti"),
                            ("charm", "Charm"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        max_length=16,
                        unique=True,
                    ),
                ),
                (
                    "interval_minutes",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (5, "5 min"),
                            (10, "10 min"),
                            (15, "15 min"),
                            (30, "30 min"),
                            (60, "60 min"),
                        ],
                        default=60,
                    ),
                ),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("next_run_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "last_status",
                    models.CharField(
                        choices=[
                            ("idle", "Idle"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("partial", "Partial"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="idle",
                        max_length=16,
                    ),
                ),
                ("last_trigger", models.CharField(blank=True, default="", max_length=16)),
                ("last_error", models.TextField(blank=True, default="")),
                ("touched_items_count", models.PositiveIntegerField(default=0)),
                ("updated_items_count", models.PositiveIntegerField(default=0)),
                ("history_written_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["category"],
                "verbose_name": "Price auto update rule",
                "verbose_name_plural": "Price auto update rules",
            },
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterule",
            index=models.Index(fields=["is_enabled", "next_run_at"], name="celery_task_is_enab_2c6ce0_idx"),
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterule",
            index=models.Index(fields=["category", "is_enabled"], name="celery_task_categor_7d193a_idx"),
        ),
    ]