from django.contrib import admin

from .models import Listing, MarketEvent, MarketSeller, PayoutRequest


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


@admin.register(PayoutRequest)
class PayoutRequestAdmin(admin.ModelAdmin):
    """Read-only: payouts are processed in Control Center → Рынок, which keeps the balances in step."""

    list_display = ("code", "user", "amount", "method", "status", "created_at", "processed_at")
    list_filter = ("status", "method")
    search_fields = ("code", "user__username")
    raw_id_fields = ("user", "processed_by")
    exclude = ("requisites",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
