from django.contrib import admin

from .models import PriceAutoUpdateRule, PriceAutoUpdateRunLog


@admin.register(PriceAutoUpdateRule)
class PriceAutoUpdateRuleAdmin(admin.ModelAdmin):
    list_display = (
        "category",
        "schedule_mode",
        "interval_minutes",
        "hourly_minute",
        "start_at",
        "is_enabled",
        "last_status",
        "last_run_at",
        "next_run_at",
        "updated_items_count",
        "history_written_count",
    )
    list_filter = ("category", "schedule_mode", "is_enabled", "last_status", "interval_minutes")
    search_fields = ("category", "last_error")
    readonly_fields = (
        "last_run_at",
        "next_run_at",
        "last_status",
        "last_trigger",
        "last_error",
        "touched_items_count",
        "updated_items_count",
        "history_written_count",
        "created_at",
        "updated_at",
    )


@admin.register(PriceAutoUpdateRunLog)
class PriceAutoUpdateRunLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rule",
        "category",
        "triggered_by",
        "status",
        "touched_items_count",
        "updated_items_count",
        "history_written_count",
        "errors_count",
        "started_at",
        "finished_at",
    )
    list_filter = ("category", "triggered_by", "status")
    search_fields = ("category", "error_summary")
    readonly_fields = (
        "rule",
        "category",
        "triggered_by",
        "status",
        "touched_items_count",
        "updated_items_count",
        "history_written_count",
        "errors_count",
        "error_summary",
        "started_at",
        "finished_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False