from django.contrib import admin

from .models import PromoActivation, PromoCode


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "kind", "activations_count", "max_activations", "expires_at", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("code", "title")
    raw_id_fields = ("owner", "created_by", "case")


@admin.register(PromoActivation)
class PromoActivationAdmin(admin.ModelAdmin):
    list_display = ("promo", "user", "status", "benefit_amount", "created_at", "used_at")
    list_filter = ("status",)
    search_fields = ("promo__code", "user__username")
    raw_id_fields = ("promo", "user", "ledger_entry")
