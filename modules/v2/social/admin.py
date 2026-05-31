from django.contrib import admin

from .models import SocialMembership, SocialSpace


class SocialMembershipInline(admin.TabularInline):
    model = SocialMembership
    extra = 0
    raw_id_fields = ("user",)
    fields = ("user", "role", "status", "joined_at", "last_read_at")
    readonly_fields = ("joined_at",)


@admin.register(SocialSpace)
class SocialSpaceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "space_type", "privacy", "owner", "is_active", "last_activity_at", "created_at")
    list_filter = ("space_type", "privacy", "is_active", "created_at")
    search_fields = ("title", "slug", "description", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("owner",)
    inlines = (SocialMembershipInline,)


@admin.register(SocialMembership)
class SocialMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "space", "user", "role", "status", "joined_at", "last_read_at")
    list_filter = ("role", "status", "joined_at")
    search_fields = ("space__title", "space__slug", "user__username", "user__email")
    readonly_fields = ("joined_at",)
    raw_id_fields = ("space", "user")
