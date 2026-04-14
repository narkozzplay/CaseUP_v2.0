from django.contrib import admin

from .models import LedgerEntry, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "role",
        "is_store_author",
        "steam_id",
        "steam_personaname",
        "faceit_skill_level",
        "faceit_elo",
        "balance_coins",
        "last_seen_at",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "steam_id",
        "steam_personaname",
        "faceit_nickname",
        "faceit_player_id",
    )
    list_filter = ("role", "is_store_author", "faceit_skill_level")
    readonly_fields = (
        "balance_coins",
        "steam_id",
        "steam_personaname",
        "steam_avatar",
        "steam_profile_url",
        "steam_profile_background",
        "steam_profile_background_video",
        "steam_background_synced_at",
        "steam_level",
        "steam_level_synced_at",
        "faceit_player_id",
        "faceit_nickname",
        "faceit_avatar_url",
        "faceit_skill_level",
        "faceit_elo",
        "faceit_level_image_url",
        "faceit_last_sync_at",
        "faceit_last_error",
        "telegram_chat_id",
        "telegram_username",
        "telegram_first_name",
        "telegram_linked_at",
        "telegram_last_interaction_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    )


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "delta", "balance_after", "reason", "created_at")
    search_fields = ("user__username", "user__email", "idempotency_key")
    list_filter = ("reason",)
    readonly_fields = (
        "user",
        "delta",
        "balance_after",
        "reason",
        "amount_snapshot",
        "case_opening_id",
        "inventory_item_id",
        "withdraw_request_id",
        "idempotency_key",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
