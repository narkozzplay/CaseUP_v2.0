from django.contrib import admin

from .models import SupportAttachment, SupportMessage, SupportTicket


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    fields = ("author", "body", "is_staff", "is_internal", "is_system", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("author",)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "user", "category", "status", "priority", "assignee", "last_message_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("subject", "user__username")
    raw_id_fields = ("user", "assignee")
    inlines = (SupportMessageInline,)


@admin.register(SupportAttachment)
class SupportAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "content_type", "size", "message", "created_at")
    raw_id_fields = ("message",)
