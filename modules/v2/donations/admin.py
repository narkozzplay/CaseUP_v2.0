from django.contrib import admin

from .models import Donation, DonationGoal


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
