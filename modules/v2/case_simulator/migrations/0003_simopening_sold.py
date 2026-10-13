from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("case_simulator", "0002_seed_cases"),
    ]

    operations = [
        migrations.AddField(
            model_name="simopening",
            name="sold_at",
            field=models.DateTimeField(blank=True, db_index=True, help_text="Продан обратно за баланс сайта.", null=True),
        ),
        migrations.AddField(
            model_name="simopening",
            name="sold_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
