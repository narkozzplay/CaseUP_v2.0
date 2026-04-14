from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ThemeStoreListing(models.Model):
    class Category(models.TextChoices):
        THEMES = "themes", "Themes"
        MODULES = "modules", "Modules"
        BUNDLES = "bundles", "Bundles"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_MODERATION = "pending_moderation", "Pending moderation"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    key = models.SlugField(max_length=80, unique=True)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.THEMES, db_index=True)

    theme_key = models.SlugField(max_length=64, blank=True, default="", db_index=True)
    module_key = models.SlugField(max_length=64, blank=True, default="", db_index=True)
    bundle_key = models.SlugField(max_length=64, blank=True, default="", db_index=True)

    title = models.CharField(max_length=140)
    short_description = models.CharField(max_length=280, blank=True, default="")
    full_description = models.TextField(blank=True, default="")

    cover_image = models.CharField(max_length=255, blank=True, default="")
    screenshots = models.JSONField(default=list, blank=True)
    author = models.CharField(max_length=120, blank=True, default="")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_theme_listings",
    )
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderated_store_listings",
    )

    price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_public = models.BooleanField(default=True, db_index=True)
    is_included_in_store_subscription = models.BooleanField(default=False, db_index=True)
    moderation_comment = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store listing"
        verbose_name_plural = "Store listings"
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=["category", "status", "is_public"], name="store_theme_list_vis_idx"),
            models.Index(fields=["theme_key", "status"], name="store_theme_list_key_idx"),
            models.Index(fields=["module_key", "status"], name="store_module_list_key_idx"),
            models.Index(fields=["bundle_key", "status"], name="store_bundle_list_key_idx"),
        ]

    def clean(self):
        self.key = (self.key or "").strip().lower()
        self.theme_key = (self.theme_key or "").strip().lower()
        self.module_key = (self.module_key or "").strip().lower()
        self.bundle_key = (self.bundle_key or "").strip().lower()
        self.title = (self.title or "").strip()
        self.short_description = (self.short_description or "").strip()
        self.full_description = (self.full_description or "").strip()
        self.cover_image = (self.cover_image or "").strip()
        self.author = (self.author or "").strip()
        self.moderation_comment = (self.moderation_comment or "").strip()

        if not self.key:
            raise ValidationError({"key": "Listing key is required."})
        if not self.title:
            raise ValidationError({"title": "Listing title is required."})
        if self.price_rub is None or self.price_rub < 0:
            raise ValidationError({"price_rub": "Price must be >= 0."})

        if self.screenshots is None:
            self.screenshots = []
        if not isinstance(self.screenshots, list):
            raise ValidationError({"screenshots": "Screenshots must be a JSON list."})
        normalized_screenshots: list[str] = []
        for item in self.screenshots:
            value = str(item or "").strip()
            if value:
                normalized_screenshots.append(value)
        self.screenshots = normalized_screenshots

        required_key_by_category = {
            self.Category.THEMES: "theme_key",
            self.Category.MODULES: "module_key",
            self.Category.BUNDLES: "bundle_key",
        }
        required_key = required_key_by_category.get(self.category, "")
        if not required_key:
            raise ValidationError({"category": "Unsupported listing category."})

        runtime_key = getattr(self, required_key, "")
        if not runtime_key:
            raise ValidationError({required_key: "Runtime key is required for selected category."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED

    @property
    def runtime_key(self) -> str:
        if self.category == self.Category.THEMES:
            return self.theme_key
        if self.category == self.Category.MODULES:
            return self.module_key
        if self.category == self.Category.BUNDLES:
            return self.bundle_key
        return ""

    def __str__(self) -> str:
        return self.title


class UserThemePurchase(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="theme_purchases",
    )
    listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    theme_key = models.SlugField(max_length=64, db_index=True)
    amount_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    purchased_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User theme purchase"
        verbose_name_plural = "User theme purchases"
        ordering = ("-purchased_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["user", "theme_key"], name="store_user_theme_purchase_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "purchased_at"], name="store_user_theme_recent_idx"),
        ]

    def clean(self):
        self.theme_key = (self.theme_key or "").strip().lower()
        if not self.theme_key:
            raise ValidationError({"theme_key": "Theme key is required."})
        if self.amount_rub is None or self.amount_rub < 0:
            raise ValidationError({"amount_rub": "Amount must be >= 0."})

    def save(self, *args, **kwargs):
        if self.listing_id and not self.theme_key:
            self.theme_key = self.listing.theme_key
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user_id}:{self.theme_key}"


class StoreBundleItem(models.Model):
    class ProductType(models.TextChoices):
        THEME = "theme", "Theme"
        MODULE = "module", "Module"

    bundle = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.CASCADE,
        related_name="bundle_items",
    )
    product_type = models.CharField(max_length=16, choices=ProductType.choices)
    product_key = models.SlugField(max_length=64)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store bundle item"
        verbose_name_plural = "Store bundle items"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["bundle", "product_type", "product_key"],
                name="store_bundle_item_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["bundle", "sort_order"], name="store_bundle_item_ord_idx"),
            models.Index(fields=["product_type", "product_key"], name="store_bundle_item_key_idx"),
        ]

    def clean(self):
        self.product_key = (self.product_key or "").strip().lower()
        if not self.product_key:
            raise ValidationError({"product_key": "Product key is required."})

        if self.bundle_id and self.bundle.category != ThemeStoreListing.Category.BUNDLES:
            raise ValidationError({"bundle": "Bundle relation expects listing with bundles category."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.bundle_id}:{self.product_type}:{self.product_key}"


class StoreProductEntitlement(models.Model):
    class Source(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        BUNDLE_PURCHASE = "bundle_purchase", "Bundle purchase"
        ADMIN = "admin", "Admin"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_entitlements",
    )
    listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.CASCADE,
        related_name="entitlements",
    )
    source = models.CharField(max_length=24, choices=Source.choices, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Store entitlement"
        verbose_name_plural = "Store entitlements"
        ordering = ("-id",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "listing", "source"],
                name="store_entitlement_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "listing"], name="store_ent_user_list_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.listing_id}:{self.source}"


class UserStorePurchase(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_purchases",
    )
    listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.PROTECT,
        related_name="purchase_records",
    )
    category = models.CharField(max_length=24, choices=ThemeStoreListing.Category.choices, db_index=True)
    product_key = models.SlugField(max_length=64, db_index=True)
    amount_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    purchased_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User store purchase"
        verbose_name_plural = "User store purchases"
        ordering = ("-purchased_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["user", "listing"], name="store_user_listing_purchase_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "purchased_at"], name="store_user_purchase_recent_idx"),
            models.Index(fields=["category", "product_key"], name="store_cat_prod_key_idx"),
        ]

    def clean(self):
        self.product_key = (self.product_key or "").strip().lower()
        if not self.product_key:
            raise ValidationError({"product_key": "Product key is required."})
        if self.amount_rub is None or self.amount_rub < 0:
            raise ValidationError({"amount_rub": "Amount must be >= 0."})

        if self.listing_id:
            if self.listing.category == ThemeStoreListing.Category.BUNDLES:
                raise ValidationError({"listing": "Bundle purchases should use UserBundlePurchase."})
            if self.category != self.listing.category:
                self.category = self.listing.category
            if not self.product_key:
                self.product_key = self.listing.runtime_key

    def save(self, *args, **kwargs):
        if self.listing_id:
            self.category = self.listing.category
            if not self.product_key:
                self.product_key = self.listing.runtime_key
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user_id}:{self.category}:{self.product_key}"


class UserBundlePurchase(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bundle_purchases",
    )
    bundle_listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.PROTECT,
        related_name="bundle_purchase_records",
    )
    bundle_key = models.SlugField(max_length=64, db_index=True)
    amount_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    purchased_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User bundle purchase"
        verbose_name_plural = "User bundle purchases"
        ordering = ("-purchased_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["user", "bundle_key"], name="store_user_bundle_purchase_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "purchased_at"], name="store_user_bundle_recent_idx"),
        ]

    def clean(self):
        self.bundle_key = (self.bundle_key or "").strip().lower()
        if not self.bundle_key:
            raise ValidationError({"bundle_key": "Bundle key is required."})
        if self.bundle_listing_id and self.bundle_listing.category != ThemeStoreListing.Category.BUNDLES:
            raise ValidationError({"bundle_listing": "Listing category must be bundles."})
        if self.amount_rub is None or self.amount_rub < 0:
            raise ValidationError({"amount_rub": "Amount must be >= 0."})

    def save(self, *args, **kwargs):
        if self.bundle_listing_id and not self.bundle_key:
            self.bundle_key = self.bundle_listing.bundle_key
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user_id}:{self.bundle_key}"


class StoreProductReview(models.Model):
    class AccessSource(models.TextChoices):
        PUBLIC = "public", "Public"
        PURCHASE = "purchase", "Purchase"
        BUNDLE_PURCHASE = "bundle_purchase", "Bundle purchase"
        SUBSCRIPTION = "subscription", "Subscription"
        ADMIN = "admin", "Admin"
        NONE = "none", "None"

    listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_reviews",
    )
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField(blank=True, default="")
    improvement_suggestion = models.TextField(blank=True, default="")
    is_visible = models.BooleanField(default=True, db_index=True)
    access_source_snapshot = models.CharField(
        max_length=24,
        choices=AccessSource.choices,
        default=AccessSource.NONE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store product review"
        verbose_name_plural = "Store product reviews"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["listing", "user"], name="store_review_listing_user_unique"),
        ]
        indexes = [
            models.Index(fields=["listing", "rating"], name="store_review_list_rat_idx"),
            models.Index(fields=["listing", "is_visible"], name="store_review_list_vis_idx"),
        ]

    def clean(self):
        self.comment = (self.comment or "").strip()
        self.improvement_suggestion = (self.improvement_suggestion or "").strip()

        if self.rating < 1 or self.rating > 5:
            raise ValidationError({"rating": "Rating must be in range 1..5."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.listing_id}:{self.user_id}:{self.rating}"


class StoreAnalyticsEvent(models.Model):
    class EventType(models.TextChoices):
        INDIVIDUAL_PURCHASE = "individual_purchase", "Individual purchase"
        BUNDLE_PURCHASE = "bundle_purchase", "Bundle purchase"
        ACTIVATION = "activation", "Activation"
        DEACTIVATION = "deactivation", "Deactivation"
        SUBSCRIPTION_USAGE = "subscription_usage", "Subscription usage"
        REVIEW_CREATED = "review_created", "Review created"

    class AccessSource(models.TextChoices):
        PUBLIC = "public", "Public"
        PURCHASE = "purchase", "Purchase"
        BUNDLE_PURCHASE = "bundle_purchase", "Bundle purchase"
        SUBSCRIPTION = "subscription", "Subscription"
        ADMIN = "admin", "Admin"
        NONE = "none", "None"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_analytics_events",
    )
    listing = models.ForeignKey(
        ThemeStoreListing,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    access_source = models.CharField(max_length=24, choices=AccessSource.choices, default=AccessSource.NONE, db_index=True)
    amount_rub = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Store analytics event"
        verbose_name_plural = "Store analytics events"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["event_type", "created_at"], name="store_event_type_idx"),
            models.Index(fields=["listing", "event_type"], name="store_event_listing_idx"),
            models.Index(fields=["access_source", "created_at"], name="store_event_access_idx"),
        ]

    def clean(self):
        if self.amount_rub is not None and self.amount_rub < 0:
            raise ValidationError({"amount_rub": "Amount must be >= 0."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.event_type}:{self.id}"
