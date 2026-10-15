from django.contrib import admin

from .models import DailyBonusClaim, DailyBonusReward, DailyBonusSettings, DailyBonusState, DailyFreeCase


@admin.register(DailyBonusSettings)
class DailyBonusSettingsAdmin(admin.ModelAdmin):
    list_display = ("is_enabled", "require_steam", "require_topup", "free_case_valid_days", "updated_at")


@admin.register(DailyBonusReward)
class DailyBonusRewardAdmin(admin.ModelAdmin):
    list_display = ("day", "kind", "amount", "case")


@admin.register(DailyBonusState)
class DailyBonusStateAdmin(admin.ModelAdmin):
    list_display = ("user", "cycle_day", "streak", "best_streak", "last_claim_date", "claims_total")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)


@admin.register(DailyBonusClaim)
class DailyBonusClaimAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "day", "kind", "amount", "case")
    list_filter = ("kind",)
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    date_hierarchy = "date"


@admin.register(DailyFreeCase)
class DailyFreeCaseAdmin(admin.ModelAdmin):
    list_display = ("user", "case", "status", "expires_at", "used_at")
    list_filter = ("status",)
    raw_id_fields = ("user", "claim")
