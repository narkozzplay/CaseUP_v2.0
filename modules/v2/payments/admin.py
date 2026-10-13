from django.contrib import admin, messages

from .models import Payment
from .providers import PaymentProviderError
from .services import sync_payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "provider", "amount", "currency", "status", "created_at", "paid_at")
    list_filter = ("provider", "status")
    search_fields = ("public_id", "external_id", "user__username", "user__email")
    readonly_fields = [field.name for field in Payment._meta.fields]
    actions = ("recheck_with_provider",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Перепроверить статус у провайдера")
    def recheck_with_provider(self, request, queryset):
        for payment in queryset:
            try:
                sync_payment(payment)
            except PaymentProviderError as exc:
                self.message_user(request, f"{payment.public_id}: {exc}", level=messages.WARNING)
        self.message_user(request, "Статусы обновлены.")
