from django.contrib import admin

from .models import TelegramNotifierConfig


@admin.register(TelegramNotifierConfig)
class TelegramNotifierConfigAdmin(admin.ModelAdmin):
    list_display = ("singleton_key", "is_enabled", "masked_bot_token", "default_chat_id", "updated_at")
    readonly_fields = ("singleton_key", "created_at", "updated_at", "masked_bot_token")
    fields = (
        "singleton_key",
        "is_enabled",
        "masked_bot_token",
        "bot_token",
        "default_chat_id",
        "investment_message_template",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        if TelegramNotifierConfig.objects.exists():
            return False
        return super().has_add_permission(request)
    