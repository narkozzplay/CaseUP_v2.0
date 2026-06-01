from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class SocialSpace(models.Model):
    class SpaceType(models.TextChoices):
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        CONVERSATION = "conversation", "Conversation"

    class Privacy(models.TextChoices):
        PRIVATE = "private", "Private"
        PROTECTED = "protected", "Protected"
        PUBLIC = "public", "Public"

    space_type = models.CharField(
        max_length=16,
        choices=SpaceType.choices,
        db_index=True,
    )
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_social_spaces",
    )
    privacy = models.CharField(
        max_length=16,
        choices=Privacy.choices,
        default=Privacy.PRIVATE,
        db_index=True,
    )
    avatar = models.ImageField(
        upload_to="social/avatars/",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Social space"
        verbose_name_plural = "Social spaces"
        constraints = [
            models.UniqueConstraint(fields=["space_type", "slug"], name="social_space_type_slug_uniq"),
        ]
        indexes = [
            models.Index(fields=["space_type", "is_active", "-last_activity_at"], name="social_sp_type_active_idx"),
            models.Index(fields=["owner", "space_type"], name="social_sp_owner_type_idx"),
            models.Index(fields=["is_active", "-last_activity_at"], name="social_sp_activity_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_social_space_slug(title=self.title, space_type=self.space_type)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_space_type_display()}: {self.title}"


class SocialMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MUTED = "muted", "Muted"
        BLOCKED = "blocked", "Blocked"
        LEFT = "left", "Left"

    space = models.ForeignKey(
        SocialSpace,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Social membership"
        verbose_name_plural = "Social memberships"
        constraints = [
            models.UniqueConstraint(fields=["space", "user"], name="social_mem_user_space_uniq"),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="social_mem_user_status_idx"),
            models.Index(fields=["space", "status"], name="social_mem_space_status_idx"),
            models.Index(fields=["user", "space"], name="social_mem_user_space_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.space_id} ({self.role}/{self.status})"


class SocialMessage(models.Model):
    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        SYSTEM = "system", "System"

    space = models.ForeignKey(
        SocialSpace,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_messages",
    )
    body = models.TextField()
    message_type = models.CharField(
        max_length=16,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Social message"
        verbose_name_plural = "Social messages"
        indexes = [
            models.Index(fields=["space", "created_at", "id"], name="social_msg_space_created_idx"),
            models.Index(fields=["space", "is_deleted", "created_at"], name="soc_msg_space_del_cr_idx"),
            models.Index(fields=["author", "created_at"], name="social_msg_author_created_idx"),
        ]

    def __str__(self) -> str:
        return f"Message {self.pk} in {self.space_id}"


def generate_unique_social_space_slug(*, title: str, space_type: str, exclude_space_id: int | None = None) -> str:
    safe_type = slugify(space_type or SocialSpace.SpaceType.DIRECT) or SocialSpace.SpaceType.DIRECT
    base = slugify(title or f"{safe_type}-space")[:150] or f"{safe_type}-space"
    slug = base
    suffix = 2
    qs = SocialSpace.objects.filter(space_type=space_type)
    if exclude_space_id:
        qs = qs.exclude(pk=exclude_space_id)

    while qs.filter(slug=slug).exists():
        marker = f"-{suffix}"
        slug = f"{base[:180 - len(marker)]}{marker}"
        suffix += 1

    return slug
