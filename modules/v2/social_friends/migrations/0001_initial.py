from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FriendRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted"), ("declined", "Declined"), ("cancelled", "Cancelled")], db_index=True, default="pending", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("from_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sent_friend_requests", to=settings.AUTH_USER_MODEL)),
                ("to_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="received_friend_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Friend request",
                "verbose_name_plural": "Friend requests",
            },
        ),
        migrations.CreateModel(
            name="Friendship",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user_a", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friendships_as_a", to=settings.AUTH_USER_MODEL)),
                ("user_b", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friendships_as_b", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Friendship",
                "verbose_name_plural": "Friendships",
            },
        ),
        migrations.CreateModel(
            name="UserBlock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("blocker", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_blocks_sent", to=settings.AUTH_USER_MODEL)),
                ("blocked", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_blocks_received", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "User block",
                "verbose_name_plural": "User blocks",
            },
        ),
        migrations.CreateModel(
            name="UserPrivacySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("who_can_send_friend_request", models.CharField(choices=[("everyone", "Everyone"), ("nobody", "Nobody")], default="everyone", max_length=16)),
                ("who_can_send_direct", models.CharField(choices=[("everyone", "Everyone"), ("friends_only", "Friends only"), ("nobody", "Nobody")], default="everyone", max_length=16)),
                ("who_can_see_online", models.CharField(choices=[("everyone", "Everyone"), ("friends_only", "Friends only"), ("nobody", "Nobody")], default="everyone", max_length=16)),
                ("who_can_see_profile", models.CharField(choices=[("everyone", "Everyone"), ("friends_only", "Friends only"), ("nobody", "Nobody")], default="everyone", max_length=16)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="privacy_settings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "User privacy settings",
                "verbose_name_plural": "User privacy settings",
            },
        ),
        migrations.AddIndex(
            model_name="friendrequest",
            index=models.Index(fields=["to_user", "status"], name="social_fr_to_status_idx"),
        ),
        migrations.AddIndex(
            model_name="friendrequest",
            index=models.Index(fields=["from_user", "status"], name="social_fr_from_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="friendrequest",
            constraint=models.UniqueConstraint(fields=["from_user", "to_user"], name="social_fr_from_to_uniq"),
        ),
        migrations.AddConstraint(
            model_name="friendrequest",
            constraint=models.CheckConstraint(condition=~models.Q(from_user=models.F("to_user")), name="social_fr_no_self"),
        ),
        migrations.AddIndex(
            model_name="friendship",
            index=models.Index(fields=["user_a", "user_b"], name="social_friendship_ab_idx"),
        ),
        migrations.AddIndex(
            model_name="friendship",
            index=models.Index(fields=["user_b", "user_a"], name="social_friendship_ba_idx"),
        ),
        migrations.AddConstraint(
            model_name="friendship",
            constraint=models.UniqueConstraint(fields=["user_a", "user_b"], name="social_friendship_pair_uniq"),
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocker", "blocked"], name="social_userblock_blocker_idx"),
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocked", "blocker"], name="social_userblock_blocked_idx"),
        ),
        migrations.AddConstraint(
            model_name="userblock",
            constraint=models.UniqueConstraint(fields=["blocker", "blocked"], name="social_userblock_pair_uniq"),
        ),
        migrations.AddConstraint(
            model_name="userblock",
            constraint=models.CheckConstraint(condition=~models.Q(blocker=models.F("blocked")), name="social_userblock_no_self"),
        ),
    ]
