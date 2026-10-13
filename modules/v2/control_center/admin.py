from django.contrib import admin

from .models import AdminAuditLog, UserBan


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "target_user", "summary", "ip_address")
    list_filter = ("action",)
    search_fields = ("summary", "actor__username", "target_user__username")
    readonly_fields = ("actor", "action", "target_user", "summary", "payload", "ip_address", "created_at")


@admin.register(UserBan)
class UserBanAdmin(admin.ModelAdmin):
    list_display = ("user", "reason", "banned_by", "created_at", "expires_at", "lifted_at")
    search_fields = ("user__username", "reason")
    raw_id_fields = ("user", "banned_by", "lifted_by")
