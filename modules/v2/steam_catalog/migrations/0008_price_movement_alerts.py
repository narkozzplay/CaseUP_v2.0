from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0007_pricedropalertrule_pricedropalertevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricedropalertrule",
            name="direction",
            field=models.CharField(
                choices=[("up", "Рост"), ("down", "Падение"), ("both", "Оба направления")],
                db_index=True,
                default="both",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="pricedropalertrule",
            name="repeat_step_percent",
            field=models.DecimalField(decimal_places=2, default=Decimal("5.00"), max_digits=7),
        ),
        migrations.AddField(
            model_name="pricedropalertrule",
            name="repeat_step_rub",
            field=models.DecimalField(decimal_places=2, default=Decimal("50.00"), max_digits=12),
        ),
        migrations.AddField(
            model_name="pricedropalertevent",
            name="baseline_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="pricedropalertevent",
            name="baseline_price_rub",
            field=models.DecimalField(decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="pricedropalertevent",
            name="direction",
            field=models.CharField(
                choices=[("up", "Рост"), ("down", "Падение")],
                db_index=True,
                default="down",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="pricedropalertevent",
            name="step_index",
            field=models.PositiveIntegerField(db_index=True, default=1),
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE steam_catalog_pricedropalertevent "
                "SET baseline_at = peak_at, baseline_price_rub = peak_price_rub, direction = 'down', step_index = 1"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="pricedropalertevent",
            name="baseline_at",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="pricedropalertevent",
            name="baseline_price_rub",
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
        migrations.RemoveField(
            model_name="pricedropalertevent",
            name="peak_at",
        ),
        migrations.RemoveField(
            model_name="pricedropalertevent",
            name="peak_price_rub",
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["rule", "item", "direction", "created_at"], name="steam_catal_rule_id_433a09_idx"),
        ),
        migrations.AddIndex(
            model_name="pricedropalertevent",
            index=models.Index(fields=["rule", "item", "direction", "step_index"], name="steam_catal_rule_id_56d007_idx"),
        ),
    ]
