from django.contrib import admin

from .models import Donation, DonationGoal, Sponsorship


@admin.register(DonationGoal)
class DonationGoalAdmin(admin.ModelAdmin):
    list_display = ("title", "is_enabled", "goal_amount", "collected_offset", "started_at", "updated_at")


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "amount", "is_anonymous", "goal_title")
    search_fields = ("user__username", "message")
    raw_id_fields = ("user",)
    readonly_fields = ("user", "amount", "message", "is_anonymous", "goal_title", "ledger_entry_id", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # A donation is backed by a ledger debit; deleting the row would only hide money from the totals.
        return False


@admin.register(Sponsorship)
class SponsorshipAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "period", "amount", "status", "expires_at", "decided_by")
    list_filter = ("kind", "status")
    search_fields = ("user__username",)
    raw_id_fields = ("user", "decided_by")
