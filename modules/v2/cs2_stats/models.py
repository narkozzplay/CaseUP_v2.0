from __future__ import annotations

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# CS2 map registry — display name + gradient color
# ---------------------------------------------------------------------------

CS2_MAPS: dict[str, dict[str, str]] = {
    "de_dust2":    {"name": "Dust II",   "color": "#B8964E", "color2": "#7A5F2A"},
    "de_mirage":   {"name": "Mirage",    "color": "#7A9DBF", "color2": "#4A6880"},
    "de_inferno":  {"name": "Inferno",   "color": "#C4612B", "color2": "#8C3D18"},
    "de_nuke":     {"name": "Nuke",      "color": "#5D8C47", "color2": "#3A5C2A"},
    "de_overpass": {"name": "Overpass",  "color": "#8B6C3E", "color2": "#5A4220"},
    "de_anubis":   {"name": "Anubis",    "color": "#C4A44A", "color2": "#8B6D28"},
    "de_ancient":  {"name": "Ancient",   "color": "#6B8C5A", "color2": "#3E5C35"},
    "de_vertigo":  {"name": "Vertigo",   "color": "#4A7A9B", "color2": "#2A4C6A"},
    "de_train":    {"name": "Train",     "color": "#8C7A5A", "color2": "#5A4A30"},
    "de_cache":    {"name": "Cache",     "color": "#7A8C6A", "color2": "#4A5C3E"},
}


class CS2PlayerStats(models.Model):
    """General CS2 stats synced from Steam GetUserStatsForGame (appid=730)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cs2_player_stats",
    )

    # Core stats
    total_kills = models.PositiveIntegerField(default=0)
    total_deaths = models.PositiveIntegerField(default=0)
    total_matches_played = models.PositiveIntegerField(default=0)
    total_wins = models.PositiveIntegerField(default=0)
    total_headshots = models.PositiveIntegerField(default=0)
    total_mvps = models.PositiveIntegerField(default=0)
    total_time_played = models.PositiveIntegerField(default=0)  # seconds
    total_shots_fired = models.PositiveIntegerField(default=0)
    total_shots_hit = models.PositiveIntegerField(default=0)
    total_damage_done = models.BigIntegerField(default=0)
    total_planted_bombs = models.PositiveIntegerField(default=0)
    total_defused_bombs = models.PositiveIntegerField(default=0)

    # Computed / cached ratios
    kd_ratio = models.FloatField(default=0.0)
    hs_percent = models.FloatField(default=0.0)
    win_rate = models.FloatField(default=0.0)
    accuracy = models.FloatField(default=0.0)

    # From GetOwnedGames
    playtime_hours = models.PositiveIntegerField(default=0)
    last_played_at = models.DateTimeField(null=True, blank=True)

    # Sync metadata
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CS2 player stats"
        verbose_name_plural = "CS2 player stats"

    def __str__(self) -> str:
        return f"CS2Stats({self.user_id})"


class CS2MapStat(models.Model):
    """Per-map stats from Steam GetUserStatsForGame."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cs2_map_stats",
    )
    map_code = models.CharField(max_length=64, db_index=True)  # e.g. "de_dust2"

    wins = models.PositiveIntegerField(default=0)
    rounds_played = models.PositiveIntegerField(default=0)
    kills = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)

    # Computed
    kd_ratio = models.FloatField(default=0.0)
    win_rate = models.FloatField(default=0.0)   # wins/rounds * 100

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CS2 map stat"
        verbose_name_plural = "CS2 map stats"
        constraints = [
            models.UniqueConstraint(fields=["user", "map_code"], name="cs2_map_user_uniq"),
        ]
        indexes = [
            models.Index(fields=["user", "wins"], name="cs2_map_user_wins_idx"),
        ]

    def __str__(self) -> str:
        return f"CS2MapStat({self.user_id}, {self.map_code})"


class FACEITExtendedStats(models.Model):
    """Extended FACEIT CS2 stats from Open Data API."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="faceit_extended_stats",
    )

    # Lifetime stats
    matches = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    avg_kd = models.FloatField(default=0.0)
    avg_hs_percent = models.FloatField(default=0.0)
    avg_kills = models.FloatField(default=0.0)
    avg_deaths = models.FloatField(default=0.0)
    avg_kr = models.FloatField(default=0.0)          # kills per round
    win_rate = models.FloatField(default=0.0)
    longest_win_streak = models.PositiveIntegerField(default=0)
    current_win_streak = models.PositiveIntegerField(default=0)

    # Activity
    last_match_at = models.DateTimeField(null=True, blank=True)

    # Sync metadata
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "FACEIT extended stats"
        verbose_name_plural = "FACEIT extended stats"

    def __str__(self) -> str:
        return f"FACEITExtended({self.user_id})"
