from __future__ import annotations

from django.conf import settings
from django.db import models


class SteamBackground(models.Model):
    title = models.CharField(max_length=160)
    static_url = models.URLField(
        help_text="URL статичного изображения (.jpg preview)",
    )
    video_url = models.URLField(
        blank=True,
        default="",
        help_text="URL анимированного видео (.webm). Пусто для статичного фона.",
    )
    source_item_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="ID предмета Steam, извлечённый из URL (например, 2861720)",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_steam_backgrounds",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Steam background"
        verbose_name_plural = "Steam backgrounds"
        indexes = [
            models.Index(fields=["is_active", "-created_at"], name="pcust_bg_active_created_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SteamBackground({self.pk}): {self.title}"


class SteamAvatar(models.Model):
    title = models.CharField(max_length=160)
    avatar_url = models.URLField(
        help_text="URL аватарки (обычно _full.jpg или анимированный формат)",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_steam_avatars",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Steam avatar"
        verbose_name_plural = "Steam avatars"
        indexes = [
            models.Index(fields=["is_active", "-created_at"], name="pcust_av_active_created_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SteamAvatar({self.pk}): {self.title}"


class UserCustomization(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customization",
    )
    selected_background = models.ForeignKey(
        SteamBackground,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_by_users",
    )
    selected_avatar = models.ForeignKey(
        SteamAvatar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_by_users",
    )
    # CaseUP-only CSS effects, keys from profile_customization.effects
    avatar_frame = models.CharField(max_length=32, blank=True, default="")
    name_effect = models.CharField(max_length=32, blank=True, default="")
    hero_effect = models.CharField(max_length=32, blank=True, default="")
    title_effect = models.CharField(max_length=32, blank=True, default="")
    accent_effect = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User customization"
        verbose_name_plural = "User customizations"

    def __str__(self) -> str:
        return f"UserCustomization(user={self.user_id})"


class EffectGrant(models.Model):
    """A reward effect the user owns: earned by an achievement, won in Pick'em or given by an admin.

    Cosmetic only: bound to the account, it cannot be sold, traded or turned into money.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="effect_grants")
    slot = models.CharField(max_length=16)
    key = models.CharField(max_length=32)
    # "achievement:<key>", "pickem:<grant id>" or "admin"
    source = models.CharField(max_length=64, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True, help_text="Когда пользователь увидел награду в каталоге.")

    class Meta:
        verbose_name = "Effect grant"
        verbose_name_plural = "Effect grants"
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "slot", "key"], name="pcust_effect_grant_uniq")]

    def __str__(self) -> str:
        return f"EffectGrant(user={self.user_id}, {self.slot}:{self.key})"


class ShowcaseItem(models.Model):
    """A skin pinned to the profile showcase, shown with its current price and weekly change."""

    MAX_ITEMS = 4

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="showcase_items")
    item = models.ForeignKey("steam_catalog.SteamItem", on_delete=models.CASCADE, related_name="+")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Showcase item"
        verbose_name_plural = "Showcase items"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["user", "item"], name="pcust_showcase_user_item_uniq")]

    def __str__(self) -> str:
        return f"ShowcaseItem(user={self.user_id}, item={self.item_id})"
