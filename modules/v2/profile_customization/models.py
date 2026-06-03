from __future__ import annotations

from django.conf import settings
from django.db import models


class SteamBackground(models.Model):
    title = models.CharField(max_length=160)
    static_url = models.URLField(
        help_text="URL статичного изображения (.jpg preview)",
    )
    video_url = models.URLField(
        help_text="URL анимированного видео (.webm)",
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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User customization"
        verbose_name_plural = "User customizations"

    def __str__(self) -> str:
        return f"UserCustomization(user={self.user_id})"
