from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("steam_trading", "0002_steampurchase_unlock_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="SteamPurchaseSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sold_at", models.DateTimeField(db_index=True)),
                ("quantity", models.PositiveIntegerField()),
                ("sale_price_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("total_sale_rub", models.DecimalField(decimal_places=2, default="0.00", max_digits=14)),
                ("cost_basis_rub", models.DecimalField(decimal_places=2, default="0.00", max_digits=14)),
                ("profit_rub", models.DecimalField(decimal_places=2, default="0.00", max_digits=14)),
                ("profit_percent", models.DecimalField(decimal_places=2, default="0.00", max_digits=7)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="steam_trading_purchase_sales",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "purchase",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sales",
                        to="steam_trading.steampurchase",
                    ),
                ),
            ],
            options={
                "ordering": ["-sold_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="steampurchasesale",
            index=models.Index(fields=["purchase", "sold_at"], name="steam_tradi_purchase_1d2b24_idx"),
        ),
        migrations.AddIndex(
            model_name="steampurchasesale",
            index=models.Index(fields=["created_by", "sold_at"], name="steam_tradi_created_ab09f8_idx"),
        ),
        migrations.AddIndex(
            model_name="steampurchasesale",
            index=models.Index(fields=["created_at"], name="steam_tradi_created_c48f14_idx"),
        ),
    ]
