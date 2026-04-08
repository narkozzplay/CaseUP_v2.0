from django.contrib import admin

from .models import EditableArea, EditablePage, PageTemplate, PageTemplateVersion, SandboxAssetBinding, SandboxSession, StylePreset, TemplateAsset, TemplateVariable, UserTemplateOwnership


class PageTemplateVersionInline(admin.TabularInline):
    model = PageTemplateVersion
    extra = 0
    fields = (
        "version_number",
        "moderation_status",
        "contains_external_links",
        "submitted_at",
        "approved_at",
        "approved_by",
    )
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(PageTemplate)
class PageTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "page_type",
        "visibility",
        "access_mode",
        "status",
        "is_free",
        "price",
        "updated_at",
    )
    list_filter = ("status", "page_type", "visibility", "access_mode", "is_free", "is_active")
    search_fields = ("title", "slug", "description", "owner__username")
    readonly_fields = ("template_uuid", "created_at", "updated_at")
    autocomplete_fields = ("owner", "current_version", "approved_version")
    inlines = [PageTemplateVersionInline]


@admin.register(PageTemplateVersion)
class PageTemplateVersionAdmin(admin.ModelAdmin):
    list_display = (
        "template",
        "version_number",
        "moderation_status",
        "contains_external_links",
        "submitted_at",
        "approved_at",
        "approved_by",
    )
    list_filter = ("moderation_status", "contains_external_links")
    search_fields = ("template__title", "template__slug", "changelog", "moderation_comment")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("template", "approved_by")


@admin.register(UserTemplateOwnership)
class UserTemplateOwnershipAdmin(admin.ModelAdmin):
    list_display = ("user", "template", "ownership_type", "active_version", "installed_at", "purchased_at")
    list_filter = ("ownership_type",)
    search_fields = ("user__username", "template__title")
    autocomplete_fields = ("user", "template", "active_version")


@admin.register(TemplateAsset)
class TemplateAssetAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "template", "asset_type", "created_at")
    list_filter = ("asset_type",)
    search_fields = ("title", "alt_text", "owner__username", "template__title")
    autocomplete_fields = ("owner", "template")


@admin.register(EditablePage)
class EditablePageAdmin(admin.ModelAdmin):
    list_display = ("title", "route_path", "url_name", "requires_auth", "is_dynamic", "is_active", "sort_order")
    list_filter = ("requires_auth", "is_dynamic", "is_active", "source_type")
    search_fields = ("title", "route_path", "url_name", "route_key", "template_name")


@admin.register(EditableArea)
class EditableAreaAdmin(admin.ModelAdmin):
    list_display = ("page", "label", "area_key", "area_type", "sort_order", "is_active")
    list_filter = ("area_type", "is_active")
    search_fields = ("page__title", "page__route_path", "label", "area_key")
    autocomplete_fields = ("page",)


@admin.register(TemplateVariable)
class TemplateVariableAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "group", "source", "value_type", "is_active")
    list_filter = ("group", "value_type", "is_active")
    search_fields = ("key", "label", "description", "resolver_hint")
    filter_horizontal = ("allowed_pages",)


@admin.register(StylePreset)
class StylePresetAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "scope", "is_default", "is_active")
    list_filter = ("scope", "is_default", "is_active")
    search_fields = ("label", "key", "description")


@admin.register(SandboxSession)
class SandboxSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "editable_page", "status", "last_saved_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("title", "owner__username", "editable_page__title")
    autocomplete_fields = ("owner", "editable_page", "legacy_template")


@admin.register(SandboxAssetBinding)
class SandboxAssetBindingAdmin(admin.ModelAdmin):
    list_display = ("session", "binding_key", "asset", "page", "area", "updated_at")
    search_fields = ("binding_key", "session__title", "session__owner__username")
    autocomplete_fields = ("session", "asset", "page", "area")
