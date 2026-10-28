from django.contrib import admin
from django.utils import timezone

from .models import LedgerEntry, UserProfile, VerificationRequest
from .services.verification import grant_verification


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
    list_filter = ("role", "verification_level", "is_store_author", "faceit_skill_level")
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
    list_display = ("id", "user", "delta", "balance_after", "reason", "comment", "created_by", "created_at")
    search_fields = ("user__username", "user__email", "idempotency_key", "comment")
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
        "comment",
        "created_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VerificationRequest)
class VerificationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "requested_level", "status",
        "created_at", "reviewed_at", "reviewed_by",
    )
    list_filter = ("status", "requested_level")
    search_fields = ("user__username", "user__email")
    readonly_fields = (
        "user", "requested_level", "user_message",
        "created_at", "reviewed_at", "reviewed_by",
    )
    fields = (
        "user", "requested_level", "status",
        "user_message", "admin_note",
        "reviewed_by", "reviewed_at", "created_at",
    )
    actions = ["approve_requests", "deny_requests"]

    @admin.action(description="✅ Одобрить выбранные заявки")
    def approve_requests(self, request, queryset):
        approved = 0
        for req in queryset.filter(status=VerificationRequest.Status.PENDING):
            req.status = VerificationRequest.Status.APPROVED
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            grant_verification(req)
            approved += 1
        self.message_user(request, f"Одобрено заявок: {approved}.")

    @admin.action(description="❌ Отклонить выбранные заявки")
    def deny_requests(self, request, queryset):
        denied = queryset.filter(status=VerificationRequest.Status.PENDING).update(
            status=VerificationRequest.Status.DENIED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Отклонено заявок: {denied}.")
