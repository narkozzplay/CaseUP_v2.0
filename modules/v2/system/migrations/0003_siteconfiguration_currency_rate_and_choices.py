from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("system_config", "0002_dashboardmenuitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="site_currency_rate",
            field=models.DecimalField(decimal_places=6, default=Decimal("1.000000"), help_text="Сколько RUB стоит 1 единица выбранной валюты.", max_digits=12),
        ),
        migrations.AlterField(
            model_name="siteconfiguration",
            name="site_currency_code",
            field=models.CharField(choices=[("RUB", "Российский рубль (RUB)"), ("USD", "Доллар США (USD)"), ("EUR", "Евро (EUR)"), ("GBP", "Фунт стерлингов (GBP)"), ("KZT", "Казахстанский тенге (KZT)"), ("BYN", "Белорусский рубль (BYN)")], default="RUB", max_length=8),
        ),
    ]
