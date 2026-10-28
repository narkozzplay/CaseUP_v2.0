from django.contrib import admin

from .models import AdPlacement, AdSettings, HouseBanner, InfoBanner


@admin.register(AdSettings)
class AdSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_enabled", "provider", "feed_every_n", "show_to_staff", "updated_at")

    def has_add_permission(self, request):
        return not AdSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdPlacement)
class AdPlacementAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_enabled", "block_id", "sort_order")
    list_editable = ("is_enabled",)
    ordering = ("sort_order", "id")
    fieldsets = (
        (None, {"fields": ("name", "key", "description", "is_enabled", "sort_order")}),
        ("Рекламная сеть (РСЯ / AdSense)", {"fields": ("block_id",), "classes": ("collapse",)}),
        ("Свой HTML-код", {"fields": ("custom_html",), "classes": ("collapse",)}),
    )


@admin.register(HouseBanner)
class HouseBannerAdmin(admin.ModelAdmin):
    list_display = ("name", "title", "url", "audience", "show_to_ad_free", "weight", "is_enabled", "impressions", "clicks")
    list_editable = ("is_enabled",)
    list_filter = ("is_enabled", "audience", "show_to_ad_free", "placements")
    search_fields = ("name", "title", "url")
    filter_horizontal = ("placements",)
    readonly_fields = ("impressions", "clicks", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "is_enabled")}),
        ("Баннер", {"fields": ("title", "text", "image", "video", "url", "button")}),
        ("Где и кому", {"fields": ("placements", "audience", "show_to_ad_free", "weight", "starts_at", "ends_at")}),
        ("Статистика", {"fields": ("impressions", "clicks", "created_at", "updated_at")}),
    )


@admin.register(InfoBanner)
class InfoBannerAdmin(admin.ModelAdmin):
    list_display = ("name", "slot", "title", "url", "audience", "sort_order", "is_enabled")
    list_editable = ("sort_order", "is_enabled")
    list_filter = ("slot", "is_enabled", "audience")
    search_fields = ("name", "title", "url")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slot", "is_enabled", "sort_order")}),
        ("Баннер", {"fields": ("eyebrow", "title", "text", "button", "url", "image", "video", "icon")}),
        ("English", {"fields": ("title_en", "text_en", "button_en"), "classes": ("collapse",)}),
        ("Кому и когда", {"fields": ("audience", "starts_at", "ends_at")}),
        ("Служебное", {"fields": ("created_at", "updated_at")}),
    )
