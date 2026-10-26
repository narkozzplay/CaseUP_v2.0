from django.contrib import admin

from .models import Referral, ReferralBonus, ReferralCode, ReferralProgramSettings, ReferralWelcomeBonus


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "clicks", "created_at")
    search_fields = ("code", "user__username")
    raw_id_fields = ("user",)


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referee", "referrer", "created_at", "qualified_at", "review", "bonus_total")
    list_filter = ("review",)
    search_fields = ("referee__username", "referrer__username", "code")
    raw_id_fields = ("referrer", "referee", "reviewed_by")


@admin.register(ReferralBonus)
class ReferralBonusAdmin(admin.ModelAdmin):
    list_display = ("referral", "sale_amount", "percent", "amount", "created_at")
    raw_id_fields = ("referral", "ledger_entry")
    readonly_fields = ("source_entry_id",)


@admin.register(ReferralProgramSettings)
class ReferralProgramSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "welcome_bonus_enabled", "welcome_bonus_days", "welcome_bonus_plan", "referrer_monthly_limit", "updated_at")

    def has_add_permission(self, request):
        return not ReferralProgramSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralWelcomeBonus)
class ReferralWelcomeBonusAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "days", "plan", "status", "mode", "reason", "created_at", "granted_at")
    list_filter = ("status", "role", "mode")
    search_fields = ("user__username", "steam_id", "referral__referrer__username", "referral__referee__username")
    raw_id_fields = ("referral", "user", "plan")
