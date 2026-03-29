from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("farm", "0007_remove_farmaccountinventoryitem_farm_farmac_account_97f06a_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmaccountinventoryitem",
            name="sale_locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="farmaccountinventoryitem",
            name="sale_state",
            field=models.CharField(
                choices=[("available", "Available"), ("reserved", "Reserved"), ("sold", "Sold")],
                db_index=True,
                default="available",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="farmaccountinventoryitem",
            name="sold_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventoryitem",
            index=models.Index(
                fields=["account", "sale_state", "last_seen_at"],
                name="farm_farmac_account_97f06a_idx",
            ),
        ),
    ]