from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class FriendRequestPrivacy(models.TextChoices):
    EVERYONE = "everyone", "Everyone"
    NOBODY = "nobody", "Nobody"


class DMPrivacy(models.TextChoices):
    EVERYONE = "everyone", "Everyone"
    FRIENDS_ONLY = "friends_only", "Friends only"
    NOBODY = "nobody", "Nobody"


class FriendRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_friend_requests",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_friend_requests",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Friend request"
        verbose_name_plural = "Friend requests"
        constraints = [
            models.UniqueConstraint(
                fields=["from_user", "to_user"],
                name="social_fr_from_to_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(from_user=F("to_user")),
                name="social_fr_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["to_user", "status"], name="social_fr_to_status_idx"),
            models.Index(fields=["from_user", "status"], name="social_fr_from_status_idx"),
        ]

    def __str__(self) -> str:
        return f"FriendRequest({self.from_user_id} → {self.to_user_id}, {self.status})"


class Friendship(models.Model):
    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_as_b",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Friendship"
        verbose_name_plural = "Friendships"
        constraints = [
            models.UniqueConstraint(
                fields=["user_a", "user_b"],
                name="social_friendship_pair_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user_a", "user_b"], name="social_friendship_ab_idx"),
            models.Index(fields=["user_b", "user_a"], name="social_friendship_ba_idx"),
        ]

    def __str__(self) -> str:
        return f"Friendship({self.user_a_id} ↔ {self.user_b_id})"


class UserBlock(models.Model):
    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_blocks_sent",
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_blocks_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User block"
        verbose_name_plural = "User blocks"
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"],
                name="social_userblock_pair_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(blocker=F("blocked")),
                name="social_userblock_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["blocker", "blocked"], name="social_userblock_blocker_idx"),
            models.Index(fields=["blocked", "blocker"], name="social_userblock_blocked_idx"),
        ]

    def __str__(self) -> str:
        return f"UserBlock({self.blocker_id} → {self.blocked_id})"


class UserPrivacySettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="privacy_settings",
    )
    who_can_send_friend_request = models.CharField(
        max_length=16,
        choices=FriendRequestPrivacy.choices,
        default=FriendRequestPrivacy.EVERYONE,
    )
    who_can_send_direct = models.CharField(
        max_length=16,
        choices=DMPrivacy.choices,
        default=DMPrivacy.EVERYONE,
    )
    who_can_see_online = models.CharField(
        max_length=16,
        choices=DMPrivacy.choices,
        default=DMPrivacy.EVERYONE,
    )
    who_can_see_profile = models.CharField(
        max_length=16,
        choices=DMPrivacy.choices,
        default=DMPrivacy.EVERYONE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User privacy settings"
        verbose_name_plural = "User privacy settings"

    def __str__(self) -> str:
        return f"PrivacySettings({self.user_id})"
