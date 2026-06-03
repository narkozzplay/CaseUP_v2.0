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
            name="CS2PlayerStats",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("total_kills", models.PositiveIntegerField(default=0)),
                ("total_deaths", models.PositiveIntegerField(default=0)),
                ("total_matches_played", models.PositiveIntegerField(default=0)),
                ("total_wins", models.PositiveIntegerField(default=0)),
                ("total_headshots", models.PositiveIntegerField(default=0)),
                ("total_mvps", models.PositiveIntegerField(default=0)),
                ("total_time_played", models.PositiveIntegerField(default=0)),
                ("total_shots_fired", models.PositiveIntegerField(default=0)),
                ("total_shots_hit", models.PositiveIntegerField(default=0)),
                ("total_damage_done", models.BigIntegerField(default=0)),
                ("total_planted_bombs", models.PositiveIntegerField(default=0)),
                ("total_defused_bombs", models.PositiveIntegerField(default=0)),
                ("kd_ratio", models.FloatField(default=0.0)),
                ("hs_percent", models.FloatField(default=0.0)),
                ("win_rate", models.FloatField(default=0.0)),
                ("accuracy", models.FloatField(default=0.0)),
                ("playtime_hours", models.PositiveIntegerField(default=0)),
                ("last_played_at", models.DateTimeField(blank=True, null=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_error", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cs2_player_stats", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "CS2 player stats", "verbose_name_plural": "CS2 player stats"},
        ),
        migrations.CreateModel(
            name="CS2MapStat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("map_code", models.CharField(db_index=True, max_length=64)),
                ("wins", models.PositiveIntegerField(default=0)),
                ("rounds_played", models.PositiveIntegerField(default=0)),
                ("kills", models.PositiveIntegerField(default=0)),
                ("deaths", models.PositiveIntegerField(default=0)),
                ("kd_ratio", models.FloatField(default=0.0)),
                ("win_rate", models.FloatField(default=0.0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cs2_map_stats", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "CS2 map stat", "verbose_name_plural": "CS2 map stats"},
        ),
        migrations.CreateModel(
            name="FACEITExtendedStats",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("matches", models.PositiveIntegerField(default=0)),
                ("wins", models.PositiveIntegerField(default=0)),
                ("avg_kd", models.FloatField(default=0.0)),
                ("avg_hs_percent", models.FloatField(default=0.0)),
                ("avg_kills", models.FloatField(default=0.0)),
                ("avg_deaths", models.FloatField(default=0.0)),
                ("avg_kr", models.FloatField(default=0.0)),
                ("win_rate", models.FloatField(default=0.0)),
                ("longest_win_streak", models.PositiveIntegerField(default=0)),
                ("current_win_streak", models.PositiveIntegerField(default=0)),
                ("last_match_at", models.DateTimeField(blank=True, null=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_error", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="faceit_extended_stats", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "FACEIT extended stats", "verbose_name_plural": "FACEIT extended stats"},
        ),
        migrations.AddConstraint(
            model_name="cs2mapstat",
            constraint=models.UniqueConstraint(fields=["user", "map_code"], name="cs2_map_user_uniq"),
        ),
        migrations.AddIndex(
            model_name="cs2mapstat",
            index=models.Index(fields=["user", "wins"], name="cs2_map_user_wins_idx"),
        ),
    ]
