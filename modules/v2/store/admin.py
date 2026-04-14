from django.contrib import admin

from .models import ThemeStoreListing, UserThemePurchase


@admin.register(ThemeStoreListing)
class ThemeStoreListingAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "theme_key", "price_rub", "status", "is_public", "updated_at")
    search_fields = ("key", "title", "theme_key", "author")
    list_filter = ("status", "is_public", "category")
    ordering = ("-updated_at", "-id")


@admin.register(UserThemePurchase)
class UserThemePurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "theme_key", "amount_rub", "purchased_at")
    search_fields = ("user__username", "theme_key", "listing__title")
    list_filter = ("theme_key",)
    readonly_fields = ("user", "listing", "theme_key", "amount_rub", "purchased_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

