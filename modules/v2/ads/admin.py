from django.contrib import admin

from .models import AdPlacement, AdSettings


@admin.register(AdSettings)
class AdSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_enabled", "provider", "feed_every_n", "show_to_staff", "updated_at")

    def has_add_permission(self, request):
        return not AdSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdPlacement)
class AdPlacementAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_enabled", "house_title", "block_id", "sort_order")
    list_editable = ("is_enabled",)
    ordering = ("sort_order", "id")
    fieldsets = (
        (None, {"fields": ("name", "key", "description", "is_enabled", "sort_order")}),
        ("Свой баннер", {"fields": ("house_title", "house_text", "house_image", "house_url", "house_button")}),
        ("Рекламная сеть (РСЯ / AdSense)", {"fields": ("block_id",), "classes": ("collapse",)}),
        ("Свой HTML-код", {"fields": ("custom_html",), "classes": ("collapse",)}),
    )
