from django.contrib import admin

from .models import (
    StoreAnalyticsEvent,
    StoreBundleItem,
    StoreProductEntitlement,
    StoreProductReview,
    ThemeStoreListing,
    UserBundlePurchase,
    UserStorePurchase,
    UserThemePurchase,
)


@admin.register(ThemeStoreListing)
class ThemeStoreListingAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "category",
        "title",
        "runtime_key",
        "price_rub",
        "status",
        "is_public",
        "is_included_in_store_subscription",
        "updated_at",
    )
    search_fields = ("key", "title", "theme_key", "module_key", "bundle_key", "author")
    list_filter = ("category", "status", "is_public", "is_included_in_store_subscription")
    ordering = ("-updated_at", "-id")


@admin.register(StoreBundleItem)
class StoreBundleItemAdmin(admin.ModelAdmin):
    list_display = ("id", "bundle", "product_type", "product_key", "sort_order")
    search_fields = ("bundle__title", "bundle__key", "product_key")
    list_filter = ("product_type",)
    ordering = ("bundle", "sort_order", "id")


@admin.register(StoreProductEntitlement)
class StoreProductEntitlementAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "listing", "source", "created_at")
    search_fields = ("user__username", "listing__title", "listing__key")
    list_filter = ("source", "listing__category")
    ordering = ("-id",)


@admin.register(UserStorePurchase)
class UserStorePurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "listing", "category", "product_key", "amount_rub", "purchased_at")
    search_fields = ("user__username", "listing__title", "product_key")
    list_filter = ("category",)
    readonly_fields = ("user", "listing", "category", "product_key", "amount_rub", "purchased_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserBundlePurchase)
class UserBundlePurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "bundle_listing", "bundle_key", "amount_rub", "purchased_at")
    search_fields = ("user__username", "bundle_listing__title", "bundle_key")
    readonly_fields = ("user", "bundle_listing", "bundle_key", "amount_rub", "purchased_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StoreProductReview)
class StoreProductReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "listing", "user", "rating", "is_visible", "access_source_snapshot", "created_at")
    search_fields = ("listing__title", "user__username", "comment", "improvement_suggestion")
    list_filter = ("rating", "is_visible", "access_source_snapshot")


@admin.register(StoreAnalyticsEvent)
class StoreAnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "access_source", "user", "listing", "amount_rub", "created_at")
    search_fields = ("user__username", "listing__title", "event_type")
    list_filter = ("event_type", "access_source")
    readonly_fields = ("user", "listing", "event_type", "access_source", "amount_rub", "payload", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
