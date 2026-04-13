from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from modules.v2.system.module_registry import MODULE_REGISTRY


ZERO_MONEY = Decimal("0.00")


class SubscriptionFeature(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    module_key = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subscription feature"
        verbose_name_plural = "Subscription features"
        ordering = ("sort_order", "name", "id")

    def __str__(self) -> str:
        return self.name

    def clean(self):
        self.code = (self.code or "").strip()
        self.name = (self.name or "").strip()
        self.module_key = (self.module_key or "").strip()

        if not self.code:
            raise ValidationError({"code": "Feature code is required."})
        if not self.name:
            raise ValidationError({"name": "Feature name is required."})
        if self.module_key and self.module_key not in MODULE_REGISTRY:
            raise ValidationError({"module_key": "Unknown module key."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class SubscriptionPlan(models.Model):
    class Family(models.TextChoices):
        SITE = "site", "Site"
        FARMER = "farmer", "Farmer"

    class Audience(models.TextChoices):
        GENERAL = "general", "General"
        FARMER = "farmer", "Farmer"

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    level = models.PositiveSmallIntegerField(unique=True)
    family = models.CharField(max_length=16, choices=Family.choices, default=Family.SITE, db_index=True)
    audience = models.CharField(max_length=16, choices=Audience.choices, default=Audience.GENERAL, db_index=True)
    description = models.TextField(blank=True, default="")
    ui_badge = models.CharField(max_length=64, blank=True, default="")
    ui_features = models.JSONField(default=list, blank=True)
    price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    price_floor_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    manual_price_adjustment_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    billing_period_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=100)
    features = models.ManyToManyField(
        SubscriptionFeature,
        blank=True,
        related_name="plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subscription plan"
        verbose_name_plural = "Subscription plans"
        ordering = ("family", "sort_order", "level", "id")
        indexes = [
            models.Index(fields=["family", "is_active", "sort_order"], name="sub_plan_fam_act_idx"),
            models.Index(fields=["audience", "family"], name="sub_plan_aud_fam_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_family_display()} · L{self.level} · {self.name}"

    def clean(self):
        self.code = (self.code or "").strip()
        self.name = (self.name or "").strip()
        self.ui_badge = (self.ui_badge or "").strip()

        if not self.code:
            raise ValidationError({"code": "Plan code is required."})
        if not self.name:
            raise ValidationError({"name": "Plan name is required."})
        if self.level < 1:
            raise ValidationError({"level": "Subscription level must be >= 1."})
        if self.ui_features is None:
            self.ui_features = []
        if not isinstance(self.ui_features, list):
            raise ValidationError({"ui_features": "UI features must be a JSON list."})
        normalized_ui_features: list[str] = []
        for item in self.ui_features:
            value = str(item).strip()
            if value:
                normalized_ui_features.append(value)
        self.ui_features = normalized_ui_features
        if self.price_rub < ZERO_MONEY:
            raise ValidationError({"price_rub": "Plan price cannot be negative."})
        if self.price_floor_rub < ZERO_MONEY:
            raise ValidationError({"price_floor_rub": "Price floor cannot be negative."})
        if self.billing_period_days <= 0:
            raise ValidationError({"billing_period_days": "Billing period must be > 0."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def ui_feature_list(self) -> list[str]:
        return [str(item).strip() for item in (self.ui_features or []) if str(item).strip()]


class SubscriptionFeaturePricingRule(models.Model):
    feature = models.ForeignKey(
        SubscriptionFeature,
        on_delete=models.CASCADE,
        related_name="pricing_rules",
    )
    family = models.CharField(max_length=16, choices=SubscriptionPlan.Family.choices, db_index=True)
    required_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="feature_pricing_rules",
        null=True,
        blank=True,
    )
    value_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    is_enabled = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subscription feature pricing rule"
        verbose_name_plural = "Subscription feature pricing rules"
        ordering = ("family", "sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["feature", "family"], name="sub_feat_rule_feature_family_uniq"),
        ]
        indexes = [
            models.Index(fields=["family", "is_enabled", "sort_order"], name="sub_feat_rule_fam_enabled_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_family_display()} · {self.feature.name}"

    def clean(self):
        errors: dict[str, str] = {}
        if self.value_rub < ZERO_MONEY:
            errors["value_rub"] = "Value cannot be negative."
        if self.required_plan_id:
            if self.required_plan.family != self.family:
                errors["required_plan"] = "Required plan must belong to the same family."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class SubscriptionPricingConfig(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    site_base_price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("149.00"))
    farmer_base_price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("249.00"))
    site_level_step_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("49.00"))
    farmer_level_step_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("99.00"))
    deep_analytics_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("129.00"))
    tg_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("89.00"))
    sales_analytics_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("139.00"))
    bonus_program_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("79.00"))
    premium_future_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("59.00"))
    reduced_commission_reference_turnover_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("3000.00"))
    reduced_commission_capture_ratio_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("40.00"))
    enhanced_benefits_weight_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("49.00"))
    global_price_floor_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("99.00"))
    reasonable_delta_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("15.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subscription pricing config"
        verbose_name_plural = "Subscription pricing config"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        money_fields = [
            "site_base_price_rub",
            "farmer_base_price_rub",
            "site_level_step_rub",
            "farmer_level_step_rub",
            "deep_analytics_weight_rub",
            "tg_weight_rub",
            "sales_analytics_weight_rub",
            "bonus_program_weight_rub",
            "premium_future_weight_rub",
            "reduced_commission_reference_turnover_rub",
            "enhanced_benefits_weight_rub",
            "global_price_floor_rub",
        ]
        errors: dict[str, str] = {}
        for field_name in money_fields:
            if getattr(self, field_name) < ZERO_MONEY:
                errors[field_name] = "Value cannot be negative."
        if self.reduced_commission_capture_ratio_percent < ZERO_MONEY:
            errors["reduced_commission_capture_ratio_percent"] = "Capture ratio cannot be negative."
        if self.reasonable_delta_percent <= ZERO_MONEY:
            errors["reasonable_delta_percent"] = "Reasonable delta must be greater than 0."
        if errors:
            raise ValidationError(errors)

    @classmethod
    def bootstrap_defaults(cls) -> dict[str, object]:
        return {}


class UserSubscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions_v2",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="user_subscriptions",
    )
    family = models.CharField(
        max_length=16,
        choices=SubscriptionPlan.Family.choices,
        default=SubscriptionPlan.Family.SITE,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    starts_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User subscription"
        verbose_name_plural = "User subscriptions"
        ordering = ("-starts_at", "-id")
        indexes = [
            models.Index(fields=["user", "status", "starts_at"], name="sub_usr_stat_start_idx"),
            models.Index(fields=["status", "expires_at"], name="sub_stat_exp_idx"),
            models.Index(fields=["plan", "status"], name="sub_plan_stat_idx"),
            models.Index(fields=["user", "family", "status"], name="sub_usr_fam_stat_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "family"],
                condition=Q(status="active"),
                name="sub_usr_family_active_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Subscription(user={self.user_id}, plan={self.plan_id}, status={self.status})"

    @property
    def is_currently_active(self) -> bool:
        now = timezone.now()
        if self.status != self.Status.ACTIVE:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.expires_at and self.expires_at <= now:
            return False
        return True

    def clean(self):
        errors: dict[str, str] = {}
        if self.status == self.Status.ACTIVE and self.expires_at and self.starts_at and self.expires_at <= self.starts_at:
            errors["expires_at"] = "Expiry datetime must be later than start datetime."

        plan_family = SubscriptionPlan.Family.SITE
        if self.plan_id:
            plan_family = self.plan.family
        if self.family and self.family != plan_family:
            errors["family"] = "Subscription family must match plan family."
        self.family = plan_family

        now = timezone.now()
        overlap_requested = (
            self.status == self.Status.ACTIVE
            and (self.starts_at is None or self.starts_at <= now)
            and (self.expires_at is None or self.expires_at > now)
        )
        if overlap_requested and self.user_id:
            overlapping_qs = (
                UserSubscription.objects
                .filter(user_id=self.user_id, status=self.Status.ACTIVE, family=self.family)
                .exclude(pk=self.pk)
                .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            )
            if overlapping_qs.exists():
                errors["status"] = (
                    "User already has another active subscription in the current time window for this plan family."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.plan_id:
            self.family = self.plan.family
        self.full_clean()
        return super().save(*args, **kwargs)
