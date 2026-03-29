from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="steamitem",
            name="item_type",
            field=models.CharField(
                choices=[
                    ("skin", "Skin"),
                    ("case", "Case"),
                    ("sticker", "Sticker"),
                    ("graffiti", "Graffiti"),
                    ("charm", "Charm"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="other",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="steamitem",
            name="exterior",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32),
        ),
    ]