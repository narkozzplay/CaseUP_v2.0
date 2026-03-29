from __future__ import annotations

from django.contrib import admin, messages

from .models import GameServer, GameServerPrivilege, GameServerStatusCheck
from .services import refresh_game_server_status


class GameServerPrivilegeInline(admin.TabularInline):
    model = GameServerPrivilege
    extra = 0
    fields = ("code", "name", "privilege_type", "price", "duration_days", "is_active", "sort_order")


class GameServerStatusCheckInline(admin.TabularInline):
    model = GameServerStatusCheck
    extra = 0
    can_delete = False
    fields = ("status", "checked_at", "latency_ms", "error")
    readonly_fields = fields
    ordering = ("-checked_at",)
    max_num = 10


@admin.register(GameServer)
class GameServerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "game",
        "host",
        "port",
        "status",
        "last_check_at",
        "last_seen_online_at",
        "is_enabled",
    )
    list_filter = ("game", "status", "is_enabled")
    search_fields = ("name", "slug", "host", "server_path")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "id")
    actions = ("refresh_status_action",)
    inlines = (GameServerPrivilegeInline, GameServerStatusCheckInline)
    fieldsets = (
        ("Main", {"fields": ("name", "slug", "game", "description", "is_enabled", "sort_order")}),
        ("Connection", {"fields": ("host", "port", "server_path")}),
        ("Status", {"fields": ("status", "last_check_at", "last_seen_online_at", "last_latency_ms", "last_error")}),
        ("Commerce / sync placeholders", {"fields": ("shop_label", "shop_description", "sync_notes")}),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ("status", "last_check_at", "last_seen_online_at", "last_latency_ms", "last_error", "created_at", "updated_at")

    @admin.action(description="Refresh selected server statuses")
    def refresh_status_action(self, request, queryset):
        refreshed = 0
        for server in queryset:
            refresh_game_server_status(server, force=True)
            refreshed += 1
        self.message_user(request, f"Status refreshed for {refreshed} server(s).", level=messages.SUCCESS)


@admin.register(GameServerPrivilege)
class GameServerPrivilegeAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "privilege_type", "price", "duration_days", "is_active")
    list_filter = ("privilege_type", "is_active", "server")
    search_fields = ("name", "code", "server__name")
    ordering = ("server", "sort_order", "id")


@admin.register(GameServerStatusCheck)
class GameServerStatusCheckAdmin(admin.ModelAdmin):
    list_display = ("server", "status", "checked_at", "latency_ms", "error")
    list_filter = ("status", "server")
    search_fields = ("server__name", "error")
    ordering = ("-checked_at", "-id")
    readonly_fields = ("server", "status", "checked_at", "latency_ms", "error")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
