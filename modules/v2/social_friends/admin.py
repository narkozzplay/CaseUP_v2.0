from django.contrib import admin

from .models import Friendship, FriendRequest, UserBlock, UserPrivacySettings


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "from_user", "to_user", "status", "created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("from_user__username", "to_user__username")
    raw_id_fields = ("from_user", "to_user")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("id", "user_a", "user_b", "created_at")
    search_fields = ("user_a__username", "user_b__username")
    raw_id_fields = ("user_a", "user_b")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    raw_id_fields = ("blocker", "blocked")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


@admin.register(UserPrivacySettings)
class UserPrivacySettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "who_can_send_friend_request",
        "who_can_send_direct",
        "who_can_see_online",
        "who_can_see_profile",
        "updated_at",
    )
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("updated_at",)
