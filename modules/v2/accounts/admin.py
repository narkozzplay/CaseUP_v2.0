from django.contrib import admin

from .models import LegalConsent


@admin.register(LegalConsent)
class LegalConsentAdmin(admin.ModelAdmin):
    """Read-only evidence of accepted documents."""

    list_display = ("user", "accepted_at", "agreement_version", "privacy_version", "pd_consent_version", "ip_address")
    list_filter = ("agreement_version", "privacy_version")
    search_fields = ("user__username", "ip_address")
    readonly_fields = [field.name for field in LegalConsent._meta.fields]
    date_hierarchy = "accepted_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
