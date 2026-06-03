from django.contrib import admin

from .models import (
    CS2MapStat,
    CS2PlayerStats,
    Cs2MatchPlayerStat,
    Cs2MatchRecord,
    Cs2MatchSyncConfig,
    FACEITExtendedStats,
)


@admin.register(CS2PlayerStats)
class CS2PlayerStatsAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "total_matches_played", "kd_ratio", "hs_percent", "win_rate", "playtime_hours", "last_synced_at")
    search_fields = ("user__username",)
    readonly_fields = tuple(f.name for f in CS2PlayerStats._meta.fields if f.name not in ("id",))
    ordering = ("-last_synced_at",)

    def has_add_permission(self, request):
        return False


@admin.register(CS2MapStat)
class CS2MapStatAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "map_code", "wins", "rounds_played", "kd_ratio", "win_rate")
    search_fields = ("user__username", "map_code")
    list_filter = ("map_code",)
    readonly_fields = tuple(f.name for f in CS2MapStat._meta.fields if f.name not in ("id",))

    def has_add_permission(self, request):
        return False


@admin.register(FACEITExtendedStats)
class FACEITExtendedStatsAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "matches", "avg_kd", "avg_hs_percent", "win_rate", "last_match_at", "last_synced_at")
    search_fields = ("user__username",)
    readonly_fields = tuple(f.name for f in FACEITExtendedStats._meta.fields if f.name not in ("id",))
    ordering = ("-last_synced_at",)

    def has_add_permission(self, request):
        return False


@admin.register(Cs2MatchSyncConfig)
class Cs2MatchSyncConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "has_auth_code", "auto_sync_enabled", "matches_imported", "last_sync_at")
    search_fields = ("user__username",)
    list_filter = ("auto_sync_enabled",)
    readonly_fields = ("created_at", "updated_at", "last_sync_at", "matches_imported")


@admin.register(Cs2MatchRecord)
class Cs2MatchRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "match_id", "map_name", "outcome", "score_own", "score_opp", "played_at")
    search_fields = ("user__username", "map_name", "share_code")
    list_filter = ("outcome", "map_name")
    readonly_fields = ("created_at",)
    ordering = ("-played_at",)

    def has_add_permission(self, request):
        return False


@admin.register(Cs2MatchPlayerStat)
class Cs2MatchPlayerStatAdmin(admin.ModelAdmin):
    list_display = ("id", "match", "steam_id", "team", "kills", "deaths", "assists", "score", "mvps")
    search_fields = ("steam_id", "match__match_id")
    list_filter = ("team",)

    def has_add_permission(self, request):
        return False
