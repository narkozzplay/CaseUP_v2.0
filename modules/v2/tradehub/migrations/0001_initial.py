from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("farm", "0006_farmaccountinventoryitem_sale_state_and_more"),
        ("steam_catalog", "0006_rename_steam_catal_item_id_9c2f8d_idx_steam_catal_item_id_6d3c16_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TradeBotAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("steam_id64", models.CharField(db_index=True, max_length=32, unique=True)),
                ("trade_url", models.URLField(blank=True, default="")),
                ("login_hint", models.CharField(blank=True, default="", max_length=120)),
                ("notes", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("active", "Active"), ("paused", "Paused"), ("review", "Review"), ("disabled", "Disabled")], db_index=True, default="review", max_length=16)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=100)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("last_offer_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "title"],
                "indexes": [models.Index(fields=["status", "is_enabled", "sort_order"], name="tradehub_tr_status_bf12c8_idx")],
            },
        ),
        migrations.CreateModel(
            name="SaleOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("draft", "Draft"), ("offer_pending", "Offer pending"), ("offer_sent", "Offer sent"), ("offer_accepted", "Offer accepted"), ("payout_pending", "Payout pending"), ("completed", "Completed"), ("cancelled_by_user", "Cancelled by user"), ("cancelled_by_system", "Cancelled by system"), ("failed", "Failed")], db_index=True, default="draft", max_length=32)),
                ("items_count", models.PositiveIntegerField(default=0)),
                ("total_catalog_price_rub", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("total_payout_rub", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("payout_paid", models.BooleanField(db_index=True, default=False)),
                ("payout_paid_at", models.DateTimeField(blank=True, null=True)),
                ("failure_reason", models.CharField(blank=True, default="", max_length=255)),
                ("cancelled_reason", models.CharField(blank=True, default="", max_length=255)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bot_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sale_orders", to="tradehub.tradebotaccount")),
                ("farm_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="trade_sale_orders", to="farm.farmaccount")),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="trade_sale_orders", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["seller", "status", "created_at"], name="tradehub_sa_seller__d01b81_idx"), models.Index(fields=["farm_account", "status", "created_at"], name="tradehub_sa_farm_ac_4f8b8e_idx"), models.Index(fields=["bot_account", "status", "created_at"], name="tradehub_sa_bot_acc_aedc2e_idx")],
            },
        ),
        migrations.CreateModel(
            name="TradeOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("new", "New"), ("sent", "Sent"), ("accepted", "Accepted"), ("cancelled", "Cancelled"), ("failed", "Failed")], db_index=True, default="new", max_length=16)),
                ("external_offer_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bot_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="offers", to="tradehub.tradebotaccount")),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="offer", to="tradehub.saleorder")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status", "created_at"], name="tradehub_tr_status_7e04cb_idx"), models.Index(fields=["external_offer_id"], name="tradehub_tr_externa_0e574d_idx")],
            },
        ),
        migrations.CreateModel(
            name="TradeEventLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.CharField(db_index=True, max_length=64)),
                ("message", models.CharField(blank=True, default="", max_length=255)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tradehub_event_logs_created", to=settings.AUTH_USER_MODEL)),
                ("offer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="logs", to="tradehub.tradeoffer")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="logs", to="tradehub.saleorder")),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [models.Index(fields=["order", "created_at"], name="tradehub_tr_order_i_06ee74_idx"), models.Index(fields=["event", "created_at"], name="tradehub_tr_event_8d81c0_idx")],
            },
        ),
        migrations.CreateModel(
            name="TradeUserPenalty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type", models.CharField(choices=[("warning", "Warning"), ("ban", "Ban")], db_index=True, max_length=16)),
                ("reason", models.CharField(max_length=255)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tradehub_penalties_created", to=settings.AUTH_USER_MODEL)),
                ("offer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="penalties", to="tradehub.tradeoffer")),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="penalties", to="tradehub.saleorder")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tradehub_penalties", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["user", "type", "is_active", "created_at"], name="tradehub_tr_user_id_55d5d4_idx")],
            },
        ),
        migrations.CreateModel(
            name="SaleOrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_id_snapshot", models.CharField(db_index=True, max_length=64)),
                ("class_id_snapshot", models.CharField(blank=True, default="", max_length=64)),
                ("instance_id_snapshot", models.CharField(blank=True, default="", max_length=64)),
                ("market_hash_name_snapshot", models.CharField(db_index=True, max_length=255)),
                ("icon_url_snapshot", models.TextField(blank=True, default="")),
                ("amount_snapshot", models.PositiveIntegerField(default=1)),
                ("catalog_price_rub_snapshot", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("payout_price_rub_snapshot", models.DecimalField(decimal_places=2, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("inventory_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="trade_sale_items", to="farm.farmaccountinventoryitem")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="tradehub.saleorder")),
                ("steam_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="trade_sale_items", to="steam_catalog.steamitem")),
            ],
            options={
                "ordering": ["id"],
                "indexes": [models.Index(fields=["order", "asset_id_snapshot"], name="tradehub_sa_order_i_19b905_idx"), models.Index(fields=["steam_item", "created_at"], name="tradehub_sa_steam_i_f8d823_idx")],
                "constraints": [models.UniqueConstraint(fields=("order", "inventory_item"), name="tradehub_unique_order_inventory_item")],
            },
        ),
    ]
