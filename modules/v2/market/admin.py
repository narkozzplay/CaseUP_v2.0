from django.contrib import admin

from .models import Listing, MarketEvent, MarketSeller


@admin.register(MarketSeller)
class MarketSellerAdmin(admin.ModelAdmin):
    list_display = ("user", "trade_url", "inventory_checked_at", "inventory_error")
    raw_id_fields = ("user",)
    search_fields = ("user__username",)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("id", "market_hash_name", "seller", "price", "status", "created_at")
    list_filter = ("status", "item_type")
    search_fields = ("market_hash_name", "asset_id", "seller__username")
    raw_id_fields = ("seller", "steam_item")


@admin.register(MarketEvent)
class MarketEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "listing", "user", "actor", "message")
    list_filter = ("kind",)
    raw_id_fields = ("listing", "user", "actor")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
