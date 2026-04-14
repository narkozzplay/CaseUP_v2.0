from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ThemeStoreListing(models.Model):
    class Category(models.TextChoices):
        THEMES = "themes", "Themes"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    key = models.SlugField(max_length=80, unique=True)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.THEMES, db_index=True)
    theme_key = models.SlugField(max_length=64, db_index=True)

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

    price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_public = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Theme store listing"
        verbose_name_plural = "Theme store listings"
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=["category", "status", "is_public"], name="store_theme_list_vis_idx"),
            models.Index(fields=["theme_key", "status"], name="store_theme_list_key_idx"),
        ]

    def clean(self):
        self.key = (self.key or "").strip().lower()
        self.theme_key = (self.theme_key or "").strip().lower()
        self.title = (self.title or "").strip()
        self.short_description = (self.short_description or "").strip()
        self.full_description = (self.full_description or "").strip()
        self.cover_image = (self.cover_image or "").strip()
        self.author = (self.author or "").strip()

        if not self.key:
            raise ValidationError({"key": "Listing key is required."})
        if not self.theme_key:
            raise ValidationError({"theme_key": "Theme key is required."})
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

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED and self.is_public

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
