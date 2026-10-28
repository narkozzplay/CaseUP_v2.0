from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import CS2_MAPS, CS2MapStat, CS2PlayerStats, FACEITExtendedStats


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CS2_APP_ID = 730
SYNC_COOLDOWN_MINUTES = 10


@dataclass
class SyncResult:
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, *, headers: dict | None = None, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")


def _steam_api_key() -> str:
    from modules.v2.system.services import get_integration_secret

    return get_integration_secret("steam_api_key")


def _faceit_api_key() -> str:
    from modules.v2.system.services import get_integration_secret

    return get_integration_secret("faceit_api_key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or 0)))
    except (ValueError, TypeError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(str(value or 0)), 4)
    except (ValueError, TypeError):
        return default


def _ratio(a: int | float, b: int | float, *, ndigits: int = 2) -> float:
    return round(a / b, ndigits) if b else 0.0


def _percent(a: int | float, b: int | float) -> float:
    return round(a / b * 100, 1) if b else 0.0


# ---------------------------------------------------------------------------
# Steam: CS2 game stats
# ---------------------------------------------------------------------------

def _fetch_steam_cs2_stats(steam_id: str) -> dict[str, int]:
    """Call GetUserStatsForGame for CS2. Returns {stat_name: int_value}."""
    api_key = _steam_api_key()
    if not api_key:
        raise ValueError("SOCIAL_AUTH_STEAM_API_KEY is not configured.")

    params = urllib.parse.urlencode({
        "appid": CS2_APP_ID,
        "steamid": steam_id,
        "key": api_key,
        "format": "json",
    })
    url = f"https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/?{params}"
    payload = _get_json(url)

    stats_raw = payload.get("playerstats", {}).get("stats", [])
    return {s["name"]: _safe_int(s.get("value")) for s in stats_raw if "name" in s}


def _fetch_steam_owned_cs2(steam_id: str) -> tuple[int, datetime | None]:
    """Returns (playtime_minutes, last_played_datetime_or_none)."""
    api_key = _steam_api_key()
    if not api_key:
        return 0, None

    params = urllib.parse.urlencode({
        "steamid": steam_id,
        "key": api_key,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }) + f"&appids_filter[0]={CS2_APP_ID}"
    url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?{params}"

    try:
        payload = _get_json(url)
    except Exception:
        return 0, None

    games = payload.get("response", {}).get("games", [])
    for game in games:
        if _safe_int(game.get("appid")) == CS2_APP_ID:
            minutes = _safe_int(game.get("playtime_forever"))
            rtime = _safe_int(game.get("rtime_last_played"))
            last_played = (
                datetime.fromtimestamp(rtime, tz=dt_timezone.utc)
                if rtime > 0 else None
            )
            return minutes, last_played
    return 0, None


def _parse_map_stats(raw: dict[str, int]) -> list[dict]:
    """Extract per-map stats from raw GetUserStatsForGame response."""
    rows = []
    for map_code in CS2_MAPS:
        safe_code = map_code.replace("-", "_")
        wins_key   = f"total_wins_map_{safe_code}"
        rounds_key = f"total_rounds_map_{safe_code}"
        kills_key  = f"total_kills_map_{safe_code}"
        deaths_key = f"total_deaths_map_{safe_code}"

        wins   = raw.get(wins_key, 0)
        rounds = raw.get(rounds_key, 0)
        kills  = raw.get(kills_key, 0)
        deaths = raw.get(deaths_key, 0)

        if rounds == 0 and wins == 0:
            continue

        rows.append({
            "map_code": map_code,
            "wins":    wins,
            "rounds_played": rounds,
            "kills":   kills,
            "deaths":  deaths,
            "kd_ratio": _ratio(kills, deaths),
            "win_rate": _percent(wins, rounds),
        })
    return rows


def sync_cs2_steam_stats(user) -> SyncResult:
    """Fetch and store CS2 Steam stats (general + per-map)."""
    from modules.v2.profiles.models import UserProfile

    try:
        profile = user.profile_v2
    except UserProfile.DoesNotExist:
        return SyncResult(False, "Profile not found.")

    steam_id = (profile.steam_id or "").strip()
    if not steam_id:
        return SyncResult(False, "Steam ID not linked.")

    # Cooldown check
    stats_obj, _ = CS2PlayerStats.objects.get_or_create(user=user)
    if stats_obj.last_synced_at:
        delta = timezone.now() - stats_obj.last_synced_at
        if delta.total_seconds() < SYNC_COOLDOWN_MINUTES * 60:
            remaining = int(SYNC_COOLDOWN_MINUTES - delta.total_seconds() // 60)
            return SyncResult(False, f"Cooldown: подождите ещё ~{remaining} мин.")

    try:
        raw_stats = _fetch_steam_cs2_stats(steam_id)
    except Exception as exc:
        stats_obj.last_sync_error = str(exc)[:500]
        stats_obj.last_synced_at = timezone.now()
        stats_obj.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
        return SyncResult(False, f"Steam API error: {exc}")

    if not raw_stats:
        msg = "Статистика CS2 недоступна (приватный профиль или нет матчей)."
        stats_obj.last_sync_error = msg
        stats_obj.last_synced_at = timezone.now()
        stats_obj.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
        return SyncResult(False, msg)

    # Hours + last played
    try:
        playtime_minutes, last_played = _fetch_steam_owned_cs2(steam_id)
    except Exception:
        playtime_minutes, last_played = 0, None

    kills   = raw_stats.get("total_kills", 0)
    deaths  = raw_stats.get("total_deaths", 0)
    hs      = raw_stats.get("total_kills_headshot", 0)  # Steam API key, not "total_headshots"
    matches = raw_stats.get("total_matches_played", 0)
    wins    = raw_stats.get("total_wins", 0)  # rounds won in Steam API, not matches won
    shots_fired = raw_stats.get("total_shots_fired", 0)
    shots_hit   = raw_stats.get("total_shots_hit", 0)

    # total_wins = rounds won; need rounds_played for a meaningful win rate
    rounds_played = raw_stats.get("total_rounds_played", 0)
    if not rounds_played:
        rounds_played = sum(
            raw_stats.get(f"total_rounds_map_{mc.replace('-', '_')}", 0)
            for mc in CS2_MAPS
        )

    with transaction.atomic():
        stats_obj.total_kills         = kills
        stats_obj.total_deaths        = deaths
        stats_obj.total_matches_played = matches
        stats_obj.total_wins          = wins
        stats_obj.total_headshots     = hs
        stats_obj.total_mvps          = raw_stats.get("total_mvps", 0)
        stats_obj.total_time_played   = raw_stats.get("total_time_played", 0)
        stats_obj.total_shots_fired   = shots_fired
        stats_obj.total_shots_hit     = shots_hit
        stats_obj.total_damage_done   = raw_stats.get("total_damage_done", 0)
        stats_obj.total_planted_bombs = raw_stats.get("total_planted_bombs", 0)
        stats_obj.total_defused_bombs = raw_stats.get("total_defused_bombs", 0)
        stats_obj.kd_ratio            = _ratio(kills, deaths)
        stats_obj.hs_percent          = _percent(hs, kills)
        stats_obj.win_rate            = _percent(wins, rounds_played)
        stats_obj.accuracy            = _percent(shots_hit, shots_fired)
        stats_obj.playtime_hours      = playtime_minutes // 60
        if last_played:
            stats_obj.last_played_at  = last_played
        stats_obj.last_synced_at      = timezone.now()
        stats_obj.last_sync_error     = ""
        stats_obj.save()

        # Per-map stats
        map_rows = _parse_map_stats(raw_stats)
        for row in map_rows:
            CS2MapStat.objects.update_or_create(
                user=user,
                map_code=row["map_code"],
                defaults={
                    "wins":         row["wins"],
                    "rounds_played": row["rounds_played"],
                    "kills":        row["kills"],
                    "deaths":       row["deaths"],
                    "kd_ratio":     row["kd_ratio"],
                    "win_rate":     row["win_rate"],
                },
            )

    return SyncResult(True, "CS2 Steam stats updated.")


# ---------------------------------------------------------------------------
# FACEIT: extended stats
# ---------------------------------------------------------------------------

def _faceit_get(path: str) -> dict:
    api_key = _faceit_api_key()
    if not api_key:
        raise ValueError("FACEIT_API_KEY is not configured.")
    url = f"https://open.faceit.com/data/v4{path}"
    return _get_json(url, headers={"Authorization": f"Bearer {api_key}"})


def sync_faceit_extended_stats(user) -> SyncResult:
    """Fetch extended FACEIT CS2 stats: lifetime + last match date."""
    from modules.v2.profiles.models import UserProfile

    try:
        profile = user.profile_v2
    except UserProfile.DoesNotExist:
        return SyncResult(False, "Profile not found.")

    player_id = (profile.faceit_player_id or "").strip()
    if not player_id:
        return SyncResult(False, "FACEIT не привязан или player_id пуст.")

    ext, _ = FACEITExtendedStats.objects.get_or_create(user=user)
    if ext.last_synced_at:
        delta = timezone.now() - ext.last_synced_at
        if delta.total_seconds() < SYNC_COOLDOWN_MINUTES * 60:
            return SyncResult(False, "Cooldown FACEIT.")

    # --- Lifetime stats ---
    try:
        lifetime_payload = _faceit_get(f"/players/{player_id}/stats/cs2")
    except Exception as exc:
        ext.last_sync_error = str(exc)[:500]
        ext.last_synced_at = timezone.now()
        ext.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
        return SyncResult(False, f"FACEIT API error: {exc}")

    lt = lifetime_payload.get("lifetime") or {}

    matches   = _safe_int(lt.get("Matches") or lt.get("matches", 0))
    wins      = _safe_int(lt.get("Wins") or lt.get("wins", 0))
    avg_kd    = _safe_float(lt.get("Average K/D Ratio") or lt.get("avg_kd", 0))
    avg_hs    = _safe_float(lt.get("Average Headshots %") or lt.get("avg_hs_pct", 0))
    avg_kr    = _safe_float(lt.get("Average K/R Ratio") or lt.get("avg_kr", 0))
    win_rate  = _safe_float(lt.get("Win Rate %") or lt.get("win_rate", 0))
    lws       = _safe_int(lt.get("Longest Win Streak") or lt.get("longest_win_streak", 0))
    cws       = _safe_int(lt.get("Current Win Streak") or lt.get("current_win_streak", 0))
    avg_kills = _safe_float(lt.get("Average Kills") or lt.get("avg_kills", 0))
    avg_deaths= _safe_float(lt.get("Average Deaths") or lt.get("avg_deaths", 0))

    # --- Last match date ---
    last_match_at = None
    try:
        history = _faceit_get(f"/players/{player_id}/history?game=cs2&limit=1&offset=0")
        items = history.get("items") or []
        if items:
            ts = _safe_int(items[0].get("started_at") or items[0].get("finished_at"))
            if ts > 0:
                last_match_at = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    except Exception:
        pass

    ext.matches             = matches
    ext.wins                = wins
    ext.avg_kd              = avg_kd
    ext.avg_hs_percent      = avg_hs
    ext.avg_kr              = avg_kr
    ext.win_rate            = win_rate
    ext.longest_win_streak  = lws
    ext.current_win_streak  = cws
    ext.avg_kills           = avg_kills
    ext.avg_deaths          = avg_deaths
    if last_match_at:
        ext.last_match_at   = last_match_at
    ext.last_synced_at      = timezone.now()
    ext.last_sync_error     = ""
    ext.save()

    return SyncResult(True, "FACEIT extended stats updated.")


# ---------------------------------------------------------------------------
# Context builder for templates
# ---------------------------------------------------------------------------

def get_cs2_stats_context(target_user, *, viewer) -> dict:
    """Build full context dict for the CS2 stats page."""
    from modules.v2.profiles.models import UserProfile
    from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name, get_verification_badge
    from modules.v2.profiles.services.presence import is_profile_online

    profile = getattr(target_user, "profile_v2", None)
    if profile is None:
        try:
            profile, _ = UserProfile.objects.get_or_create(user=target_user)
        except Exception:
            profile = None

    display_name = get_profile_display_name(profile, fallback_username=target_user.username)
    avatar_url   = get_profile_avatar_url(profile)
    badge        = get_verification_badge(profile)
    is_online    = is_profile_online(profile)
    is_owner     = (getattr(viewer, "pk", None) == target_user.pk)

    # CS2 Steam stats
    steam_stats = None
    try:
        steam_stats = CS2PlayerStats.objects.get(user=target_user)
    except CS2PlayerStats.DoesNotExist:
        pass

    # Map stats — sorted by wins desc, only known maps
    map_stats_qs = (
        CS2MapStat.objects
        .filter(user=target_user)
        .order_by("-wins")
    )
    map_rows = []
    for ms in map_stats_qs:
        map_info = CS2_MAPS.get(ms.map_code, {
            "name": ms.map_code.replace("de_", "").title(),
            "color": "#4A5568",
            "color2": "#2D3748",
        })
        map_rows.append({
            "map_code": ms.map_code,
            "map_name": map_info["name"],
            "color":    map_info["color"],
            "color2":   map_info["color2"],
            "wins":          ms.wins,
            "rounds_played": ms.rounds_played,
            "kills":         ms.kills,
            "deaths":        ms.deaths,
            "kd_ratio":      ms.kd_ratio,
            "win_rate":      ms.win_rate,
        })

    # FACEIT extended
    faceit_ext = None
    try:
        faceit_ext = FACEITExtendedStats.objects.get(user=target_user)
    except FACEITExtendedStats.DoesNotExist:
        pass

    # Can sync?
    can_sync = is_owner
    has_steam = bool(getattr(profile, "steam_id", None))
    has_faceit = bool(getattr(profile, "faceit_player_id", None))

    # Comparison data
    comparison = None
    if steam_stats and faceit_ext:
        comparison = {
            "steam_kd":       steam_stats.kd_ratio,
            "faceit_kd":      faceit_ext.avg_kd,
            "steam_hs":       steam_stats.hs_percent,
            "faceit_hs":      faceit_ext.avg_hs_percent,
            "steam_winrate":  steam_stats.win_rate,
            "faceit_winrate": faceit_ext.win_rate,
            "steam_matches":  steam_stats.total_matches_played,
            "faceit_matches": faceit_ext.matches,
            "steam_last":     steam_stats.last_played_at,
            "faceit_last":    faceit_ext.last_match_at,
        }

    steam_has_data = bool(steam_stats and (steam_stats.total_matches_played or steam_stats.total_kills))
    faceit_level = int(getattr(profile, "faceit_skill_level", 0) or 0)
    faceit_elo = int(getattr(profile, "faceit_elo", 0) or 0)
    try:
        from modules.v2.profile_customization.services import get_profile_effect_classes

        profile_effects = get_profile_effect_classes(target_user)
    except Exception:
        profile_effects = {}

    return {
        "profile_effects": profile_effects,
        "steam_has_data": steam_has_data,
        "faceit_level":  faceit_level,
        "faceit_elo":    faceit_elo,
        "faceit_progress": faceit_level_progress(faceit_elo, faceit_level),
        "headline":      _headline_stats(steam_stats if steam_has_data else None, faceit_ext),
        "target_user":   target_user,
        "display_name":  display_name,
        "avatar_url":    avatar_url,
        "badge":         badge,
        "is_online":     is_online,
        "profile":       profile,
        "is_owner":      is_owner,
        "can_sync":      can_sync,
        "has_steam":     has_steam,
        "has_faceit":    has_faceit,
        "steam_stats":   steam_stats,
        "faceit_ext":    faceit_ext,
        "map_rows":      map_rows,
        "comparison":    comparison,
    }


# FACEIT CS2 ELO thresholds: level N starts at FACEIT_LEVEL_FLOORS[N - 1].
FACEIT_LEVEL_FLOORS = (100, 501, 751, 901, 1051, 1201, 1351, 1531, 1751, 2001)


def faceit_level_progress(elo: int, level: int) -> dict | None:
    """How far the player is through the current FACEIT level, for the progress bar."""
    if not level or not elo:
        return None
    level = max(1, min(10, level))
    floor = FACEIT_LEVEL_FLOORS[level - 1]
    if level == 10:
        return {"percent": 100, "next_level": None, "elo_to_next": 0}
    ceiling = FACEIT_LEVEL_FLOORS[level]
    percent = round(max(0, min(1, (elo - floor) / (ceiling - floor))) * 100)
    return {"percent": percent, "next_level": level + 1, "elo_to_next": max(0, ceiling - elo)}


def _headline_stats(steam_stats, faceit_ext) -> list[dict]:
    """The four big numbers on top: FACEIT when the player has it, otherwise Steam MM."""
    if faceit_ext and faceit_ext.matches:
        source = "FACEIT"
        rows = [
            ("K/D", "K/D", faceit_ext.avg_kd, ""),
            ("HS %", "HS %", faceit_ext.avg_hs_percent, "%"),
            ("Винрейт", "Win rate", faceit_ext.win_rate, "%"),
            ("Матчей", "Matches", faceit_ext.matches, ""),
        ]
    elif steam_stats:
        source = "Steam MM"
        rows = [
            ("K/D", "K/D", steam_stats.kd_ratio, ""),
            ("HS %", "HS %", steam_stats.hs_percent, "%"),
            ("Раунды выиграны", "Round wins", steam_stats.win_rate, "%"),
            ("Матчей", "Matches", steam_stats.total_matches_played, ""),
        ]
    else:
        return []
    return [{"label_ru": ru, "label_en": en, "value": value, "unit": unit, "source": source} for ru, en, value, unit in rows]
