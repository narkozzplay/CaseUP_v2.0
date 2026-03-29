from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0005_steamitempricehistory"),
        ("farm", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ActiveDropPoolEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=100)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="active_drop_entries",
                        to="steam_catalog.steamitem",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "item__market_hash_name"],
            },
        ),
        migrations.CreateModel(
            name="WeeklyDropLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_index", models.PositiveIntegerField(db_index=True)),
                ("week_start", models.DateField(db_index=True)),
                ("week_end", models.DateField(db_index=True)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weekly_drops",
                        to="farm.farmaccount",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="farm_weekly_drops_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "item_1",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="weekly_drops_as_first",
                        to="steam_catalog.steamitem",
                    ),
                ),
                (
                    "item_2",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="weekly_drops_as_second",
                        to="steam_catalog.steamitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-week_index", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="activedroppoolentry",
            index=models.Index(fields=["is_active", "sort_order"], name="farm_active_is_acti_188955_idx"),
        ),
        migrations.AddIndex(
            model_name="weeklydroplog",
            index=models.Index(fields=["account", "week_index"], name="farm_weekly_account_623d62_idx"),
        ),
        migrations.AddIndex(
            model_name="weeklydroplog",
            index=models.Index(fields=["week_start", "week_end"], name="farm_weekly_week_st_293c3e_idx"),
        ),
        migrations.AddConstraint(
            model_name="activedroppoolentry",
            constraint=models.UniqueConstraint(fields=("item",), name="farm_unique_active_drop_pool_item"),
        ),
        migrations.AddConstraint(
            model_name="weeklydroplog",
            constraint=models.UniqueConstraint(fields=("account", "week_index"), name="farm_unique_account_drop_week"),
        ),
    ]