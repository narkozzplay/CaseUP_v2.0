from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("celery_tasks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceAutoUpdateRunLog",
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
                    ),
                ),
                ("triggered_by", models.CharField(blank=True, db_index=True, default="", max_length=16)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("idle", "Idle"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("partial", "Partial"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="running",
                        max_length=16,
                    ),
                ),
                ("touched_items_count", models.PositiveIntegerField(default=0)),
                ("updated_items_count", models.PositiveIntegerField(default=0)),
                ("history_written_count", models.PositiveIntegerField(default=0)),
                ("errors_count", models.PositiveIntegerField(default=0)),
                ("error_summary", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(db_index=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="run_logs",
                        to="celery_tasks.priceautoupdaterule",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at", "-id"],
                "verbose_name": "Price auto update run log",
                "verbose_name_plural": "Price auto update run logs",
            },
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterunlog",
            index=models.Index(fields=["rule", "-started_at"], name="celery_task_rule_id_e8a664_idx"),
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterunlog",
            index=models.Index(fields=["category", "-started_at"], name="celery_task_categor_4dc70a_idx"),
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterunlog",
            index=models.Index(fields=["status", "-started_at"], name="celery_task_status_4f54ec_idx"),
        ),
        migrations.AddIndex(
            model_name="priceautoupdaterunlog",
            index=models.Index(fields=["triggered_by", "-started_at"], name="celery_task_trigger_2bdfec_idx"),
        ),
    ]