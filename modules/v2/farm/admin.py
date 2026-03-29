from django.contrib import admin

from .models import (
    ActiveDropPoolEntry,
    FarmAccount,
    FarmAccountInventoryItem,
    FarmAccountInventorySync,
    FarmDropItem,
    FarmDropSale,
    FarmDropSession,
    FarmModuleConfig,
    FarmerRewardGrant,
    FarmerRewardTier,
    FarmNotificationEvent,
    WeeklyDropLog,
)


@admin.register(FarmAccount)
class FarmAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "owner", "status", "steam_id64", "sort_order", "created_at")
    search_fields = ("title", "steam_id64", "login_hint", "owner__username")
    list_filter = ("status",)


@admin.register(FarmAccountInventorySync)
class FarmAccountInventorySyncAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "status", "total_assets", "total_sellable", "started_at", "finished_at")
    search_fields = ("account__title", "account__steam_id64", "error_text")
    list_filter = ("status",)


@admin.register(FarmAccountInventoryItem)
class FarmAccountInventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "account",
        "asset_id",
        "market_hash_name",
        "is_sellable",
        "is_missing",
        "matched_item",
        "catalog_price_rub_snapshot",
        "payout_price_rub_snapshot",
        "last_seen_at",
    )
    search_fields = ("account__title", "asset_id", "market_hash_name", "matched_item__market_hash_name")
    list_filter = ("is_sellable", "is_missing", "tradable", "marketable")


@admin.register(WeeklyDropLog)
class WeeklyDropLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "account",
        "week_index",
        "week_start",
        "week_end",
        "item_1",
        "item_2",
        "created_by",
        "created_at",
    )
    search_fields = (
        "account__title",
        "item_1__market_hash_name",
        "item_2__market_hash_name",
    )
    list_filter = ("week_index", "week_start")


@admin.register(ActiveDropPoolEntry)
class ActiveDropPoolEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "sort_order", "is_active", "updated_at")
    search_fields = ("item__market_hash_name", "item__name", "item__name_ru")
    list_filter = ("is_active", "item__item_type")


class FarmDropItemInline(admin.TabularInline):
    model = FarmDropItem
    extra = 0
    readonly_fields = ("owner", "status", "remaining_quantity", "created_at", "updated_at")


@admin.register(FarmDropSession)
class FarmDropSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "owner", "source_type", "status", "collected_at", "created_at")
    search_fields = ("title", "notes", "account__title", "owner__username")
    list_filter = ("source_type", "status")
    inlines = [FarmDropItemInline]


@admin.register(FarmDropItem)
class FarmDropItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "market_hash_name_snapshot",
        "quantity",
        "remaining_quantity",
        "collected_price_rub_snapshot",
        "status",
    )
    search_fields = ("market_hash_name_snapshot", "name_snapshot", "session__account__title")
    list_filter = ("status",)


@admin.register(FarmDropSale)
class FarmDropSaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item",
        "platform",
        "quantity",
        "gross_sale_rub",
        "net_sale_rub",
        "profit_rub",
        "sold_at",
    )
    search_fields = ("item__market_hash_name_snapshot", "session__account__title", "notes")
    list_filter = ("platform",)


@admin.register(FarmModuleConfig)
class FarmModuleConfigAdmin(admin.ModelAdmin):
    list_display = (
        "singleton_key",
        "default_caseup_fee_percent",
        "subscribed_caseup_fee_percent",
        "external_reference_fee_percent",
        "price_up_threshold_percent",
        "price_down_threshold_percent",
        "updated_at",
    )


@admin.register(FarmerRewardTier)
class FarmerRewardTierAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "threshold_turnover_rub", "bonus_value_rub", "sort_order", "is_active")
    search_fields = ("code", "title", "reward_title")
    list_filter = ("is_active",)


@admin.register(FarmerRewardGrant)
class FarmerRewardGrantAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "tier", "turnover_rub_snapshot", "granted_at")
    search_fields = ("owner__username", "tier__code", "tier__title")
    list_filter = ("tier",)


@admin.register(FarmNotificationEvent)
class FarmNotificationEventAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "event_type", "item", "sale", "sent_at")
    search_fields = ("owner__username", "item__market_hash_name_snapshot", "sale__notes")
    list_filter = ("event_type",)
