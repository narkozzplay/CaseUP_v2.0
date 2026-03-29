from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("steam_catalog", "0008_price_movement_alerts"),
    ]

    operations = [
        migrations.CreateModel(
            name="SteamPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purchased_at", models.DateTimeField(db_index=True)),
                ("quantity", models.PositiveIntegerField()),
                ("purchase_price_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("total_spent_rub", models.DecimalField(decimal_places=2, default="0.00", max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="steam_trading_purchases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="trading_purchases",
                        to="steam_catalog.steamitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-purchased_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="steampurchase",
            index=models.Index(fields=["created_by", "purchased_at"], name="steam_tradi_created_3ef912_idx"),
        ),
        migrations.AddIndex(
            model_name="steampurchase",
            index=models.Index(fields=["item", "purchased_at"], name="steam_tradi_item_id_e7dfbe_idx"),
        ),
        migrations.AddIndex(
            model_name="steampurchase",
            index=models.Index(fields=["created_at"], name="steam_tradi_created_908ff8_idx"),
        ),
    ]
