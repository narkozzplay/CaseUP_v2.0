from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0008_userprofile_telegram_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ledgerentry",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AlterField(
            model_name="ledgerentry",
            name="reason",
            field=models.CharField(
                choices=[
                    ("topup", "Top up"),
                    ("case_open", "Case open"),
                    ("item_sale", "Item sale"),
                    ("withdraw_lock", "Withdraw lock"),
                    ("withdraw_cancel", "Withdraw cancel"),
                    ("admin_adjust", "Admin adjust"),
                    ("subscription_purchase", "Subscription purchase"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                fields=("user", "idempotency_key"),
                name="profiles_ledger_user_idempotency_uniq",
            ),
        ),
    ]
