from django.contrib import admin

from .models import (
    PriceDropAlertEvent,
    PriceDropAlertRule,
    SteamItem,
    SteamItemPriceHistory,
    StockMovement,
    StockReason,
    UserPriceAlertEvent,
    UserPriceAlertRule,
)


@admin.register(SteamItem)
class SteamItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "market_hash_name",
        "item_type",
        "price_rub",
        "stock",
        "allow_buy",
        "allow_sell",
        "is_active",
        "needs_review",
    )
    search_fields = ("market_hash_name", "name", "name_ru", "weapon")
    list_filter = ("item_type", "is_active", "needs_review", "allow_buy", "allow_sell")


@admin.register(SteamItemPriceHistory)
class SteamItemPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "price_rub", "source", "created_at")
    search_fields = ("item__market_hash_name", "item__family_name")
    list_filter = ("source", "created_at")


@admin.register(PriceDropAlertRule)
class PriceDropAlertRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item_type",
        "direction",
        "is_enabled",
        "lookback_hours",
        "min_drop_rub",
        "min_drop_percent",
        "repeat_step_rub",
        "repeat_step_percent",
        "cooldown_minutes",
        "notify_transport",
        "updated_at",
    )
    list_filter = ("item_type", "direction", "is_enabled", "notify_transport")
    search_fields = ("item_type",)


@admin.register(PriceDropAlertEvent)
class PriceDropAlertEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item",
        "rule",
        "direction",
        "step_index",
        "status",
        "notify_transport",
        "drop_rub",
        "drop_percent",
        "trigger_source",
        "created_at",
        "sent_at",
    )
    list_filter = ("status", "direction", "notify_transport", "trigger_source", "created_at")
    search_fields = ("item__market_hash_name",)


@admin.register(StockReason)
class StockReasonAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "direction", "is_active", "sort_order")
    list_filter = ("direction", "is_active")
    search_fields = ("name",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "delta", "reason", "created_by", "created_at")
    search_fields = ("item__market_hash_name", "note")
    list_filter = ("reason", "created_at")

@admin.register(UserPriceAlertRule)
class UserPriceAlertRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "name", "scope_type", "item_type", "direction", "reference_point",
        "threshold_mode", "window_kind", "window_value", "status", "delivery_target", "last_sent_at",
    )
    list_filter = ("scope_type", "item_type", "direction", "status", "transport", "delivery_target")
    search_fields = ("name", "family_name", "user__username", "item__market_hash_name")


@admin.register(UserPriceAlertEvent)
class UserPriceAlertEventAdmin(admin.ModelAdmin):
    list_display = (
        "id", "rule", "user", "item", "direction", "reference_point", "change_rub", "change_percent",
        "step_index", "delivery_status", "sent_to", "created_at", "sent_at",
    )
    list_filter = ("delivery_status", "direction", "reference_point", "transport", "delivery_target")
    search_fields = ("rule__name", "user__username", "item__market_hash_name", "sent_to")
