from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0004_steamitem_steam_catal_family__d760f1_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SteamItemPriceHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price_rub", models.DecimalField(decimal_places=2, max_digits=12)),
                ("source", models.CharField(blank=True, default="manual", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="price_history",
                        to="steam_catalog.steamitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="steamitempricehistory",
            index=models.Index(fields=["item", "created_at"], name="steam_catal_item_id_9c2f8d_idx"),
        ),
        migrations.AddIndex(
            model_name="steamitempricehistory",
            index=models.Index(fields=["created_at"], name="steam_catal_created_7a7fd1_idx"),
        ),
    ]