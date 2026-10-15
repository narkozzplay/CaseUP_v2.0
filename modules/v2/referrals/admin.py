from django.contrib import admin

from .models import Referral, ReferralBonus, ReferralCode


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
