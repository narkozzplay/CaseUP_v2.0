from django.contrib import admin

from .models import IpBan, MultiAccountCase, UserIp


@admin.register(UserIp)
class UserIpAdmin(admin.ModelAdmin):
    list_display = ("user", "ip", "first_seen", "last_seen", "hits")
    search_fields = ("ip", "user__username")
    raw_id_fields = ("user",)


@admin.register(MultiAccountCase)
class MultiAccountCaseAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_at", "updated_at")
    list_filter = ("status",)


@admin.register(IpBan)
class IpBanAdmin(admin.ModelAdmin):
    list_display = ("network", "reason", "created_at", "expires_at", "lifted_at")
    search_fields = ("network", "reason")
    raw_id_fields = ("created_by", "lifted_by", "case")
