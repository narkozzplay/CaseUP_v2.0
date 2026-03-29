from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .dashboard_menu import ensure_dashboard_menu_items
from .models import DashboardMenuItem, ModuleState, SiteConfiguration
from .module_registry import MODULE_REGISTRY
from .services import get_module_state_map, update_module_states


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "site_name",
        "site_currency_code",
        "support_email",
        "public_base_url",
        "maintenance_mode_enabled",
        "registration_enabled",
        "updated_at",
    )
    list_filter = ("maintenance_mode_enabled", "registration_enabled", "updated_at")
    search_fields = ("site_name", "support_email", "public_base_url")
    readonly_fields = (
        "singleton_key",
        "created_at",
        "updated_at",
        "tg_notifier_enabled",
        "steam_catalog_enabled",
    )
    fieldsets = (
        (
            "General",
            {
                "fields": (
                    "singleton_key",
                    "site_name",
                    "site_currency_code",
                    "site_currency_symbol",
                    "support_email",
                    "public_base_url",
                )
            },
        ),
        (
            "Operational flags",
            {
                "fields": (
                    "registration_enabled",
                    "maintenance_mode_enabled",
                    "maintenance_message",
                    "tg_notifier_enabled",
                    "steam_catalog_enabled",
                )
            },
        ),
        (
            "Meta",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ModuleState)
class ModuleStateAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "dependencies_display", "dependents_display", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("key", "label", "dependencies_display", "dependents_display", "updated_at")
    fields = ("key", "label", "dependencies_display", "dependents_display", "is_enabled", "updated_at")
    ordering = ("key",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Dependencies")
    def dependencies_display(self, obj: ModuleState) -> str:
        definition = MODULE_REGISTRY.get(obj.key)
        if not definition or not definition.dependencies:
            return "—"
        return ", ".join(MODULE_REGISTRY[item].label for item in definition.dependencies)

    @admin.display(description="Dependents")
    def dependents_display(self, obj: ModuleState) -> str:
        dependent_labels = [
            definition.label
            for definition in MODULE_REGISTRY.values()
            if obj.key in definition.dependencies
        ]
        if not dependent_labels:
            return "—"
        return ", ".join(dependent_labels)

    def save_model(self, request, obj, form, change):
        proposed = get_module_state_map()
        proposed[obj.key] = obj.is_enabled
        try:
            update_module_states(proposed)
        except ValidationError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            raise


@admin.register(DashboardMenuItem)
class DashboardMenuItemAdmin(admin.ModelAdmin):
    list_display = ("key", "label_ru", "url_name", "group", "visibility_condition", "is_enabled", "sort_order")
    list_filter = ("group", "visibility_condition", "is_enabled")
    search_fields = ("key", "label_ru", "label_en", "url_name")
    ordering = ("group", "sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "key",
        "label_ru",
        "label_en",
        "url_name",
        "group",
        "visibility_condition",
        "is_enabled",
        "sort_order",
        "created_at",
        "updated_at",
    )

    def changelist_view(self, request, extra_context=None):
        ensure_dashboard_menu_items()
        return super().changelist_view(request, extra_context=extra_context)
