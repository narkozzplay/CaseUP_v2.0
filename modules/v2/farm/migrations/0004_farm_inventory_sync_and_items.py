from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0006_rename_steam_catal_item_id_9c2f8d_idx_steam_catal_item_id_6d3c16_idx_and_more"),
        ("farm", "0003_rename_farm_active_is_acti_188955_idx_farm_active_is_acti_3e4a48_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="FarmAccountInventorySync",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("success", "Success"), ("failed", "Failed")], db_index=True, default="pending", max_length=16)),
                ("total_assets", models.PositiveIntegerField(default=0)),
                ("total_sellable", models.PositiveIntegerField(default=0)),
                ("error_text", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_sync_runs", to="farm.farmaccount")),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="FarmAccountInventoryItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_id", models.CharField(max_length=64)),
                ("class_id", models.CharField(blank=True, default="", max_length=64)),
                ("instance_id", models.CharField(blank=True, default="", max_length=64)),
                ("amount", models.PositiveIntegerField(default=1)),
                ("market_hash_name", models.CharField(db_index=True, max_length=255)),
                ("icon_url", models.TextField(blank=True, default="")),
                ("tradable", models.BooleanField(db_index=True, default=False)),
                ("marketable", models.BooleanField(db_index=True, default=False)),
                ("catalog_price_rub_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("payout_price_rub_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("is_sellable", models.BooleanField(db_index=True, default=False)),
                ("sell_block_reason", models.CharField(blank=True, default="", max_length=255)),
                ("is_missing", models.BooleanField(db_index=True, default=False)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_items", to="farm.farmaccount")),
                ("matched_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="farm_inventory_matches", to="steam_catalog.steamitem")),
                ("sync_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="items", to="farm.farmaccountinventorysync")),
            ],
            options={
                "ordering": ["-is_sellable", "market_hash_name", "asset_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="farmaccountinventoryitem",
            constraint=models.UniqueConstraint(fields=("account", "asset_id"), name="farm_unique_account_asset_id"),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventorysync",
            index=models.Index(fields=["account", "status"], name="farm_inven_account_87ee62_idx"),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventorysync",
            index=models.Index(fields=["account", "started_at"], name="farm_inven_account_22171d_idx"),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventoryitem",
            index=models.Index(fields=["account", "is_missing", "is_sellable"], name="farm_inven_account_4dd39f_idx"),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventoryitem",
            index=models.Index(fields=["account", "market_hash_name"], name="farm_inven_account_22765d_idx"),
        ),
        migrations.AddIndex(
            model_name="farmaccountinventoryitem",
            index=models.Index(fields=["account", "last_seen_at"], name="farm_inven_account_4a4e0f_idx"),
        ),
    ]