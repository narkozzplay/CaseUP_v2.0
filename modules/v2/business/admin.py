from django.contrib import admin

from .models import RevenueEntry


@admin.register(RevenueEntry)
class RevenueEntryAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "category", "source", "amount", "user", "ref")
    list_filter = ("category", "source")
    search_fields = ("ref", "user__username", "note")
    raw_id_fields = ("user",)
    date_hierarchy = "occurred_at"

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
