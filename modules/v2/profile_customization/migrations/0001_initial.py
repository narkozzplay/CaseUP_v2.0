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
            name="SteamBackground",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("static_url", models.URLField(help_text="URL статичного изображения (.jpg preview)")),
                ("video_url", models.URLField(help_text="URL анимированного видео (.webm)")),
                ("source_item_id", models.CharField(blank=True, default="", help_text="ID предмета Steam, извлечённый из URL (например, 2861720)", max_length=32)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("added_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="added_steam_backgrounds", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Steam background",
                "verbose_name_plural": "Steam backgrounds",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SteamAvatar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("avatar_url", models.URLField(help_text="URL аватарки (обычно _full.jpg или анимированный формат)")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("added_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="added_steam_avatars", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Steam avatar",
                "verbose_name_plural": "Steam avatars",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="UserCustomization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="customization", to=settings.AUTH_USER_MODEL)),
                ("selected_background", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="selected_by_users", to="profile_customization.steambackground")),
                ("selected_avatar", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="selected_by_users", to="profile_customization.steamavatar")),
            ],
            options={
                "verbose_name": "User customization",
                "verbose_name_plural": "User customizations",
            },
        ),
        migrations.AddIndex(
            model_name="steambackground",
            index=models.Index(fields=["is_active", "-created_at"], name="pcust_bg_active_created_idx"),
        ),
        migrations.AddIndex(
            model_name="steamavatar",
            index=models.Index(fields=["is_active", "-created_at"], name="pcust_av_active_created_idx"),
        ),
    ]
