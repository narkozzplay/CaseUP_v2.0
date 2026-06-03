from django.contrib import admin

from .models import SteamAvatar, SteamBackground, UserCustomization


@admin.register(SteamBackground)
class SteamBackgroundAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "source_item_id", "is_active", "added_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "source_item_id")
    raw_id_fields = ("added_by",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(SteamAvatar)
class SteamAvatarAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_active", "added_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title",)
    raw_id_fields = ("added_by",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(UserCustomization)
class UserCustomizationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "selected_background", "selected_avatar", "updated_at")
    raw_id_fields = ("user", "selected_background", "selected_avatar")
    readonly_fields = ("updated_at",)
