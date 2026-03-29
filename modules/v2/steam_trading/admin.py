from django.contrib import admin

from .models import SteamPurchase, SteamPurchaseSale


@admin.register(SteamPurchase)
class SteamPurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item",
        "purchased_at",
        "unlocked_at",
        "unlock_notified_at",
        "quantity",
        "purchase_price_rub",
        "total_spent_rub",
        "created_by",
        "created_at",
    )
    list_filter = ("purchased_at", "unlocked_at", "unlock_notified_at", "created_at", "item__item_type")
    search_fields = (
        "item__market_hash_name",
        "item__name",
        "item__name_ru",
        "created_by__username",
        "unlock_notification_error",
    )
    ordering = ("-purchased_at", "-id")
    readonly_fields = ("total_spent_rub", "unlocked_at", "unlock_notified_at", "unlock_notification_error", "created_at", "updated_at")
    autocomplete_fields = ("item", "created_by")


@admin.register(SteamPurchaseSale)
class SteamPurchaseSaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "purchase",
        "sold_at",
        "quantity",
        "sale_price_rub",
        "total_sale_rub",
        "cost_basis_rub",
        "profit_rub",
        "profit_percent",
        "created_by",
        "created_at",
    )
    list_filter = ("sold_at", "created_at", "purchase__item__item_type")
    search_fields = (
        "purchase__item__market_hash_name",
        "purchase__item__name",
        "purchase__item__name_ru",
        "created_by__username",
    )
    ordering = ("-sold_at", "-id")
    readonly_fields = ("total_sale_rub", "cost_basis_rub", "profit_rub", "profit_percent", "created_at", "updated_at")
    autocomplete_fields = ("purchase", "created_by")
