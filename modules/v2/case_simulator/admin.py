from django.contrib import admin

from .models import SimCase, SimCaseItem, SimOpening, TerminalSession


class SimCaseItemInline(admin.TabularInline):
    model = SimCaseItem
    extra = 0
    fields = ("name", "weapon", "rarity", "value", "weight", "min_float", "max_float", "stattrak_allowed", "image_url", "sort_order")


@admin.register(SimCase)
class SimCaseAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "case_price", "key_price", "is_active", "sort_order")
    list_filter = ("kind", "is_active")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "slug", "collection_name")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [SimCaseItemInline]


@admin.register(SimOpening)
class SimOpeningAdmin(admin.ModelAdmin):
    list_display = ("user", "case", "item", "source", "exterior", "is_stattrak", "price_paid", "item_value", "created_at")
    list_filter = ("source", "case")
    search_fields = ("user__username", "item__name")
    raw_id_fields = ("user", "item")


@admin.register(TerminalSession)
class TerminalSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "case", "status", "current_index", "price_paid", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("user", "opening")
