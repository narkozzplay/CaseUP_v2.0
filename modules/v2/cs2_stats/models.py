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


class Cs2MatchSyncConfig(models.Model):
    """Stores user's Steam Game Auth Code for automatic match import."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cs2_match_sync_config",
    )
    auth_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Steam Game Authentication Code from CS2 settings (e.g. XXXX-XXXXX-XXXX)",
    )
    last_known_match_token = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Last synced match sharing code — starting point for next sync",
    )
    auto_sync_enabled = models.BooleanField(default=False)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True, default="")
    matches_imported = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CS2 match sync config"
        verbose_name_plural = "CS2 match sync configs"

    def __str__(self) -> str:
        return f"MatchSyncConfig({self.user_id})"

    @property
    def has_auth_code(self) -> bool:
        return bool(self.auth_code and self.auth_code.strip())


class Cs2MatchRecord(models.Model):
    """A single CS2 matchmaking match imported via share-code API."""

    class Outcome(models.TextChoices):
        WIN = "win", "Win"
        LOSS = "loss", "Loss"
        TIE = "tie", "Tie"
        UNKNOWN = "unknown", "Unknown"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cs2_matches",
    )
    match_id = models.BigIntegerField(db_index=True)
    share_code = models.CharField(max_length=64, blank=True, default="")

    # Match info
    map_name = models.CharField(max_length=64, blank=True, default="")
    played_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    outcome = models.CharField(
        max_length=10, choices=Outcome.choices, default=Outcome.UNKNOWN
    )
    score_own = models.SmallIntegerField(default=0)
    score_opp = models.SmallIntegerField(default=0)

    # Raw API response for future parsing
    raw_data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "CS2 match record"
        verbose_name_plural = "CS2 match records"
        constraints = [
            models.UniqueConstraint(fields=["user", "match_id"], name="cs2_match_user_uniq"),
        ]
        indexes = [
            models.Index(fields=["user", "-played_at"], name="cs2_match_user_played_idx"),
        ]
        ordering = ["-played_at"]

    def __str__(self) -> str:
        return f"Match({self.match_id}, {self.user_id})"

    @property
    def map_display(self) -> str:
        return CS2_MAPS.get(self.map_name, {}).get("name", self.map_name.replace("de_", "").title())

    @property
    def outcome_color_class(self) -> str:
        return {"win": "metric-positive", "loss": "metric-negative", "tie": "metric-neutral"}.get(
            self.outcome, ""
        )


class Cs2MatchPlayerStat(models.Model):
    """Per-player statistics for a single CS2 match."""

    match = models.ForeignKey(
        Cs2MatchRecord,
        on_delete=models.CASCADE,
        related_name="player_stats",
    )
    steam_id = models.CharField(max_length=24, db_index=True)
    team = models.SmallIntegerField(default=0)   # 2 = CT start, 3 = T start in CS2

    kills = models.SmallIntegerField(default=0)
    deaths = models.SmallIntegerField(default=0)
    assists = models.SmallIntegerField(default=0)
    headshot_kills = models.SmallIntegerField(default=0)
    score = models.SmallIntegerField(default=0)
    mvps = models.SmallIntegerField(default=0)
    ping = models.SmallIntegerField(default=0)
    rank_after = models.SmallIntegerField(default=0, null=True, blank=True)

    class Meta:
        verbose_name = "CS2 match player stat"
        verbose_name_plural = "CS2 match player stats"
        constraints = [
            models.UniqueConstraint(
                fields=["match", "steam_id"], name="cs2_matchplayer_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"PlayerStat({self.match_id}, {self.steam_id})"

    @property
    def kd(self) -> float:
        return round(self.kills / self.deaths, 2) if self.deaths else float(self.kills)

    @property
    def hs_percent(self) -> float:
        return round(self.headshot_kills / self.kills * 100, 1) if self.kills else 0.0


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
