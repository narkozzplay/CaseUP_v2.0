from django.contrib import admin

from .models import SubscriptionFeature, SubscriptionFeaturePricingRule, SubscriptionPlan, SubscriptionPricingConfig, UserSubscription


@admin.register(SubscriptionFeature)
class SubscriptionFeatureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "module_key", "free_tier", "is_active", "sort_order")
    list_filter = ("is_active", "free_tier", "module_key")
    search_fields = ("code", "name", "description")
    ordering = ("sort_order", "name", "id")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "family", "audience", "level", "price_rub", "code", "is_active", "sort_order")
    list_filter = ("is_active", "family", "audience")
    search_fields = ("code", "name", "description")
    ordering = ("family", "sort_order", "level", "id")
    filter_horizontal = ("features",)


@admin.register(SubscriptionPricingConfig)
class SubscriptionPricingConfigAdmin(admin.ModelAdmin):
    list_display = ("singleton_key", "farmer_base_price_rub", "farmer_level_step_rub", "reasonable_delta_percent", "updated_at")

    def has_add_permission(self, request):
        return not SubscriptionPricingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "starts_at", "expires_at", "is_currently_active", "expiry_reminder_sent_at")
    list_filter = ("status", "plan", "plan__family")
    search_fields = ("user__username", "plan__name", "notes")
    ordering = ("-starts_at", "-id")
    raw_id_fields = ("user", "plan")


@admin.register(SubscriptionFeaturePricingRule)
class SubscriptionFeaturePricingRuleAdmin(admin.ModelAdmin):
    list_display = ("feature", "family", "required_plan", "value_rub", "is_enabled", "sort_order")
    list_filter = ("family", "is_enabled")
    search_fields = ("feature__code", "feature__name", "required_plan__name")
    ordering = ("family", "sort_order", "id")

