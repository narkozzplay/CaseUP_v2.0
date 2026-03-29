from django.contrib import admin

from .models import SaleOrder, SaleOrderItem, TradeBotAccount, TradeEventLog, TradeOffer, TradeUserPenalty


@admin.register(TradeBotAccount)
class TradeBotAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "steam_id64", "status", "is_enabled", "is_default", "sort_order", "last_offer_at")
    list_filter = ("status", "is_enabled", "is_default")
    search_fields = ("title", "steam_id64", "login_hint")


class SaleOrderItemInline(admin.TabularInline):
    model = SaleOrderItem
    extra = 0
    readonly_fields = (
        "inventory_item",
        "steam_item",
        "asset_id_snapshot",
        "market_hash_name_snapshot",
        "payout_price_rub_snapshot",
    )


@admin.register(SaleOrder)
class SaleOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "seller", "farm_account", "bot_account", "status", "items_count", "total_payout_rub", "payout_paid", "created_at")
    list_filter = ("status", "payout_paid")
    search_fields = ("seller__username", "farm_account__title", "bot_account__title")
    inlines = [SaleOrderItemInline]


@admin.register(TradeOffer)
class TradeOfferAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "bot_account", "status", "external_offer_id", "last_checked_at", "closed_at")
    list_filter = ("status",)
    search_fields = ("external_offer_id", "order__id", "bot_account__title")


@admin.register(TradeEventLog)
class TradeEventLogAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "offer", "event", "message", "created_by", "created_at")
    list_filter = ("event",)
    search_fields = ("message", "order__id", "offer__external_offer_id")


@admin.register(TradeUserPenalty)
class TradeUserPenaltyAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "is_active", "order", "created_at")
    list_filter = ("type", "is_active")
    search_fields = ("user__username", "reason")
