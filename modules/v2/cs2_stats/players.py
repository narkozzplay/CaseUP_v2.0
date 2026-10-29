"""Lookup of any CS2 player by a Steam / FACEIT link, like csstats.

Every player somebody searched is kept in TrackedPlayer and refreshed by the
`cs2_stats.refresh_tracked_players` task, so the player base keeps growing.

Limits worth knowing:
  * Steam returns matchmaking stats and hours only when the profile and its
    game details are public; the summary and bans are always available.
  * The FACEIT Data API has no ELO history. The best ELO ever is read from the
    ELO history FACEIT's own site uses (undocumented, best effort); when that
    is unavailable it falls back to the highest ELO CaseUP has seen.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import TrackedPlayer, FaceitMatch
from .services import (
    _faceit_api_key,
    _fetch_steam_cs2_stats,
    _fetch_steam_owned_cs2,
    _get_json,
    _parse_map_stats,
    _percent,
    _ratio,
    _safe_float,
    _safe_int,
    _steam_api_key,
)


logger = logging.getLogger(__name__)

STEAM_ID64_BASE = 76561197960265728
REFRESH_COOLDOWN_MINUTES = 10
# New players a single user may pull from the APIs per window; repeat views of known players are free.
LOOKUP_LIMIT = 30
LOOKUP_WINDOW_SECONDS = 10 * 60
MATCH_PAGE_SIZE = 100
MAX_MATCH_PAGES = 300           # 30 000 matches, far beyond any real player
MAX_ELO_HISTORY_PAGES = 300
PAGE_DELAY_SECONDS = 0.3
FACEIT_ELO_HISTORY_URL = "https://www.faceit.com/api/stats/v1/stats/time/users/{player_id}/games/cs2?page={page}&size={size}"


class PlayerNotFound(Exception):
    pass


@dataclass
class LookupResult:
    ok: bool
    message: str
    player: TrackedPlayer | None = None
    created: bool = False


# ---------------------------------------------------------------------------
# Parsing what the user typed
# ---------------------------------------------------------------------------

_STEAM2_RE = re.compile(r"^STEAM_[0-5]:([01]):(\d+)$", re.I)
_STEAM3_RE = re.compile(r"^\[?U:1:(\d+)\]?$", re.I)
_PROFILES_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})", re.I)
_VANITY_RE = re.compile(r"steamcommunity\.com/id/([\w\-.]+)", re.I)
_FACEIT_RE = re.compile(r"faceit\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?players?/([\w\-.]+)", re.I)
_STEAM64_RE = re.compile(r"^7656119\d{10}$")


def parse_player_query(raw: str) -> tuple[str, str]:
    """Returns (kind, value): kind is steam_id, vanity, faceit or text."""
    value = (raw or "").strip()
    if not value:
        return "", ""
    if _STEAM64_RE.match(value):
        return "steam_id", value
    match = _STEAM2_RE.match(value)
    if match:
        return "steam_id", str(STEAM_ID64_BASE + int(match.group(2)) * 2 + int(match.group(1)))
    match = _STEAM3_RE.match(value)
    if match:
        return "steam_id", str(STEAM_ID64_BASE + int(match.group(1)))
    match = _PROFILES_RE.search(value)
    if match:
        return "steam_id", match.group(1)
    match = _VANITY_RE.search(value)
    if match:
        return "vanity", match.group(1)
    match = _FACEIT_RE.search(value)
    if match:
        return "faceit", match.group(1)
    return "text", value


def is_steam_id64(value: str) -> bool:
    return bool(_STEAM64_RE.match(value or ""))


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------

def _steam_get(path: str, params: dict) -> dict:
    api_key = _steam_api_key()
    if not api_key:
        raise ValueError("SOCIAL_AUTH_STEAM_API_KEY is not configured.")
    query = urllib.parse.urlencode({**params, "key": api_key, "format": "json"})
    return _get_json(f"https://api.steampowered.com/{path}?{query}")


def resolve_vanity(vanity: str) -> str:
    payload = _steam_get("ISteamUser/ResolveVanityURL/v1/", {"vanityurl": vanity})
    response = payload.get("response") or {}
    if _safe_int(response.get("success")) == 1:
        return str(response.get("steamid") or "")
    return ""


def _fetch_steam_summary(steam_id: str) -> dict:
    payload = _steam_get("ISteamUser/GetPlayerSummaries/v2/", {"steamids": steam_id})
    players = (payload.get("response") or {}).get("players") or []
    return players[0] if players else {}


def _fetch_steam_bans(steam_id: str) -> dict:
    payload = _steam_get("ISteamUser/GetPlayerBans/v1/", {"steamids": steam_id})
    players = payload.get("players") or []
    return players[0] if players else {}


def _steam_stats_snapshot(raw: dict[str, int]) -> dict:
    kills = raw.get("total_kills", 0)
    deaths = raw.get("total_deaths", 0)
    headshots = raw.get("total_kills_headshot", 0)
    rounds_won = raw.get("total_wins", 0)
    rounds = raw.get("total_rounds_played", 0)
    shots_fired = raw.get("total_shots_fired", 0)
    return {
        "kills": kills,
        "deaths": deaths,
        "kd": _ratio(kills, deaths),
        "hs_percent": _percent(headshots, kills),
        "matches": raw.get("total_matches_played", 0),
        "matches_won": raw.get("total_matches_won", 0),
        "rounds_won_percent": _percent(rounds_won, rounds),
        "mvps": raw.get("total_mvps", 0),
        "accuracy": _percent(raw.get("total_shots_hit", 0), shots_fired),
        "damage": raw.get("total_damage_done", 0),
        "bombs_planted": raw.get("total_planted_bombs", 0),
        "bombs_defused": raw.get("total_defused_bombs", 0),
        "maps": _parse_map_stats(raw),
    }


def refresh_steam_part(player: TrackedPlayer) -> None:
    summary = _fetch_steam_summary(player.steam_id)
    if not summary:
        raise PlayerNotFound("Steam profile not found.")
    player.persona_name = (summary.get("personaname") or "")[:128]
    player.avatar_url = summary.get("avatarfull") or summary.get("avatarmedium") or ""
    player.profile_url = summary.get("profileurl") or ""
    player.country = (summary.get("loccountrycode") or "")[:8]
    player.is_public = _safe_int(summary.get("communityvisibilitystate")) == 3
    created = _safe_int(summary.get("timecreated"))
    player.steam_created_at = datetime.fromtimestamp(created, tz=dt_timezone.utc) if created else None

    try:
        bans = _fetch_steam_bans(player.steam_id)
        player.vac_bans = _safe_int(bans.get("NumberOfVACBans"))
        player.game_bans = _safe_int(bans.get("NumberOfGameBans"))
        player.days_since_last_ban = _safe_int(bans.get("DaysSinceLastBan")) if (player.vac_bans or player.game_bans) else 0
    except Exception as exc:
        logger.info("steam bans for %s: %s", player.steam_id, exc)

    player.steam_error = ""
    if not player.is_public:
        player.steam_error = "private"
        return
    try:
        raw = _fetch_steam_cs2_stats(player.steam_id)
    except urllib.error.HTTPError as exc:
        # Steam answers 403/500 when only the game details are hidden.
        player.steam_error = "private" if exc.code in (400, 403, 500) else f"Steam HTTP {exc.code}"
        raw = {}
    if raw:
        player.steam_stats = _steam_stats_snapshot(raw)
    elif not player.steam_error:
        player.steam_error = "private"
    try:
        minutes, _last_played = _fetch_steam_owned_cs2(player.steam_id)
        if minutes:
            player.playtime_hours = minutes // 60
    except Exception:
        pass


# ---------------------------------------------------------------------------
# FACEIT
# ---------------------------------------------------------------------------

def _faceit_get(path: str, params: dict | None = None) -> dict:
    api_key = _faceit_api_key()
    if not api_key:
        raise ValueError("FACEIT_API_KEY is not configured.")
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return _get_json(
        f"https://open.faceit.com/data/v4{path}{query}",
        headers={"Authorization": f"Bearer {api_key}"},
    )


def _faceit_player_by_steam(steam_id: str) -> dict:
    try:
        return _faceit_get("/players", {"game": "cs2", "game_player_id": steam_id})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def _faceit_player_by_nickname(nickname: str) -> dict:
    try:
        return _faceit_get("/players", {"nickname": nickname})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def _faceit_cs2(payload: dict) -> dict:
    games = payload.get("games") or {}
    return games.get("cs2") or {}


def steam_id_from_faceit_nickname(nickname: str) -> str:
    payload = _faceit_player_by_nickname(nickname)
    return str(_faceit_cs2(payload).get("game_player_id") or "")


def _parse_lifetime(payload: dict) -> tuple[dict, list[dict]]:
    lt = payload.get("lifetime") or {}
    lifetime = {
        "matches": _safe_int(lt.get("Matches")),
        "wins": _safe_int(lt.get("Wins")),
        "win_rate": _safe_float(lt.get("Win Rate %")),
        "kd": _safe_float(lt.get("Average K/D Ratio")),
        "hs_percent": _safe_float(lt.get("Average Headshots %")),
        "adr": _safe_float(lt.get("ADR")),
        "longest_win_streak": _safe_int(lt.get("Longest Win Streak")),
        "current_win_streak": _safe_int(lt.get("Current Win Streak")),
        "recent_results": [str(r) for r in (lt.get("Recent Results") or [])][:5],
    }
    maps = []
    for segment in payload.get("segments") or []:
        if (segment.get("type") or "").lower() != "map":
            continue
        stats = segment.get("stats") or {}
        matches = _safe_int(stats.get("Matches"))
        if not matches:
            continue
        maps.append({
            "map_code": segment.get("label") or "",
            "matches": matches,
            "wins": _safe_int(stats.get("Wins")),
            "win_rate": _safe_float(stats.get("Win Rate %")),
            "kd": _safe_float(stats.get("Average K/D Ratio")),
            "hs_percent": _safe_float(stats.get("Average Headshots %")),
        })
    maps.sort(key=lambda row: row["matches"], reverse=True)
    return lifetime, maps


def _note_elo(player: TrackedPlayer, elo: int, *, at: datetime | None = None) -> None:
    if elo and elo > player.faceit_max_elo:
        player.faceit_max_elo = elo
        player.faceit_max_elo_at = at or timezone.now()
        if not player.faceit_max_elo_source:
            player.faceit_max_elo_source = TrackedPlayer.MaxEloSource.TRACKED


def refresh_faceit_part(player: TrackedPlayer) -> None:
    payload = _faceit_player_by_steam(player.steam_id)
    game = _faceit_cs2(payload)
    if not payload or not game:
        player.faceit_player_id = ""
        player.faceit_error = "no_account"
        return
    player.faceit_player_id = payload.get("player_id") or ""
    player.faceit_nickname = (payload.get("nickname") or "")[:128]
    player.faceit_avatar_url = payload.get("avatar") or ""
    player.faceit_country = (payload.get("country") or "")[:8]
    player.faceit_level = _safe_int(game.get("skill_level"))
    player.faceit_elo = _safe_int(game.get("faceit_elo"))
    player.faceit_error = ""
    _note_elo(player, player.faceit_elo)
    try:
        player.faceit_lifetime, player.faceit_maps = _parse_lifetime(
            _faceit_get(f"/players/{player.faceit_player_id}/stats/cs2")
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    number = _safe_int(value)
    if number:
        if number > 10**12:     # milliseconds
            number //= 1000
        return datetime.fromtimestamp(number, tz=dt_timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)


def _match_from_stats_item(item: dict) -> dict | None:
    stats = item.get("stats") or item
    match_id = stats.get("Match Id") or item.get("match_id")
    if not match_id:
        return None
    result = stats.get("Result")
    return {
        "match_id": str(match_id),
        "played_at": _parse_timestamp(stats.get("Match Finished At") or stats.get("Updated At") or stats.get("Created At")),
        "map_name": (stats.get("Map") or "")[:64],
        "won": None if result in (None, "") else str(result) == "1",
        "score": (stats.get("Score") or "").replace(" ", "")[:16],
        "kills": _safe_int(stats.get("Kills")),
        "deaths": _safe_int(stats.get("Deaths")),
        "assists": _safe_int(stats.get("Assists")),
        "headshots_percent": _safe_float(stats.get("Headshots %")),
        "kd_ratio": _safe_float(stats.get("K/D Ratio")),
        "kr_ratio": _safe_float(stats.get("K/R Ratio")),
        "adr": _safe_float(stats.get("ADR")),
        "mvps": _safe_int(stats.get("MVPs")),
    }


def _fetch_match_page(player: TrackedPlayer, offset: int) -> list[dict]:
    payload = _faceit_get(
        f"/players/{player.faceit_player_id}/games/cs2/stats",
        {"offset": offset, "limit": MATCH_PAGE_SIZE},
    )
    rows = []
    for item in payload.get("items") or []:
        row = _match_from_stats_item(item)
        if row:
            rows.append(row)
    return rows


def import_faceit_matches(player: TrackedPlayer, *, max_pages: int = MAX_MATCH_PAGES) -> int:
    """Stores the player's FACEIT matches; returns how many were new.

    Until the whole history is in, every run continues where the previous one
    stopped. After that only the newest pages are read, until a known match.
    """
    if not player.faceit_player_id:
        return 0
    known = set(player.faceit_matches.values_list("match_id", flat=True))
    complete = player.faceit_history_complete
    new_total = 0
    # New matches at the top shift older ones down, so resuming at the stored count overlaps and never skips.
    offset = 0 if complete else max(0, len(known) - MATCH_PAGE_SIZE)
    for page in range(max_pages):
        try:
            rows = _fetch_match_page(player, offset)
        except urllib.error.HTTPError as exc:
            if offset and exc.code in (400, 404):
                # FACEIT refuses offsets past what it keeps: everything reachable is stored.
                logger.info("faceit matches %s stop at offset %s: HTTP %s", player.faceit_player_id, offset, exc.code)
                player.faceit_history_complete = True
                break
            raise
        fresh = [row for row in rows if row["match_id"] not in known]
        if fresh:
            FaceitMatch.objects.bulk_create(
                [FaceitMatch(player=player, **row) for row in fresh],
                ignore_conflicts=True,
            )
            known.update(row["match_id"] for row in fresh)
            new_total += len(fresh)
        if len(rows) < MATCH_PAGE_SIZE:
            player.faceit_history_complete = True
            break
        if complete and len(fresh) < len(rows):
            break
        offset += MATCH_PAGE_SIZE
        if page + 1 < max_pages:
            time.sleep(PAGE_DELAY_SECONDS)
    return new_total


def _fetch_elo_history_page(player_id: str, page: int) -> list[dict]:
    url = FACEIT_ELO_HISTORY_URL.format(player_id=player_id, page=page, size=MATCH_PAGE_SIZE)
    payload = _get_json(url, headers={"User-Agent": "Mozilla/5.0 (CaseUP stats)", "Accept": "application/json"})
    return payload if isinstance(payload, list) else []


def import_faceit_elo_history(player: TrackedPlayer, *, max_pages: int = MAX_ELO_HISTORY_PAGES) -> bool:
    """ELO after each match, from FACEIT's site. Returns False when that source is unavailable."""
    if not player.faceit_player_id:
        return False
    elo_by_match: dict[str, tuple[int, datetime | None]] = {}
    for page in range(max_pages):
        try:
            rows = _fetch_elo_history_page(player.faceit_player_id, page)
        except Exception as exc:
            logger.info("faceit elo history %s page %s: %s", player.faceit_player_id, page, exc)
            if page == 0:
                return False
            break
        for row in rows:
            elo = _safe_int(row.get("elo"))
            match_id = row.get("matchId") or row.get("match_id")
            if elo and match_id:
                elo_by_match[str(match_id)] = (elo, _parse_timestamp(row.get("date") or row.get("created_at")))
        if len(rows) < MATCH_PAGE_SIZE:
            break
        time.sleep(PAGE_DELAY_SECONDS)
    if not elo_by_match:
        return False

    matches = list(player.faceit_matches.filter(match_id__in=elo_by_match.keys()))
    for match in matches:
        match.elo = elo_by_match[match.match_id][0]
    FaceitMatch.objects.bulk_update(matches, ["elo"], batch_size=500)

    best_match_id, (best_elo, best_at) = max(elo_by_match.items(), key=lambda item: item[1][0])
    if best_elo >= player.faceit_max_elo:
        player.faceit_max_elo = best_elo
        player.faceit_max_elo_at = best_at or player.faceit_max_elo_at
    player.faceit_max_elo_source = TrackedPlayer.MaxEloSource.HISTORY
    player.faceit_elo_history_checked_at = timezone.now()
    return True


# ---------------------------------------------------------------------------
# Refresh + lookup
# ---------------------------------------------------------------------------

def _sync_profile_max_elo(player: TrackedPlayer) -> None:
    """CaseUP users with this Steam account get the best ELO on their profile too."""
    if not player.faceit_max_elo:
        return
    from modules.v2.profiles.models import UserProfile

    UserProfile.objects.filter(steam_id=player.steam_id, faceit_max_elo__lt=player.faceit_max_elo).update(
        faceit_max_elo=player.faceit_max_elo
    )


def refresh_player(player: TrackedPlayer, *, match_pages: int = 1) -> TrackedPlayer:
    """Steam + FACEIT profile and the newest matches. The full backfill runs in the background task."""
    refresh_steam_part(player)
    try:
        refresh_faceit_part(player)
    except Exception as exc:
        player.faceit_error = str(exc)[:300]
        logger.info("faceit refresh %s: %s", player.steam_id, exc)
    player.last_synced_at = timezone.now()
    player.save()

    if player.faceit_player_id and match_pages:
        try:
            if import_faceit_matches(player, max_pages=match_pages) or player.faceit_history_complete:
                player.save(update_fields=["faceit_history_complete"])
        except Exception as exc:
            logger.info("faceit matches %s: %s", player.steam_id, exc)
    _sync_profile_max_elo(player)
    return player


def backfill_player(player: TrackedPlayer) -> dict:
    """Whole FACEIT match history + ELO history. Heavy: only from Celery."""
    lock_key = f"cs2:backfill:{player.pk}"
    if not cache.add(lock_key, 1, timeout=30 * 60):
        return {"skipped": "locked"}
    try:
        new_matches = import_faceit_matches(player)
        elo_ok = import_faceit_elo_history(player)
        # Also stamped when FACEIT's site refuses: retried weekly, not on every view.
        player.faceit_elo_history_checked_at = timezone.now()
        stored_peak = player.faceit_matches.aggregate(peak=Max("elo"))["peak"] or 0
        if stored_peak > player.faceit_max_elo:
            player.faceit_max_elo = stored_peak
        player.save()
        _sync_profile_max_elo(player)
        return {"new_matches": new_matches, "elo_history": elo_ok}
    finally:
        cache.delete(lock_key)


def needs_backfill(player: TrackedPlayer) -> bool:
    if not player.faceit_player_id:
        return False
    if not player.faceit_history_complete:
        return True
    checked = player.faceit_elo_history_checked_at
    return checked is None or checked < timezone.now() - timedelta(days=7)


def schedule_backfill(player: TrackedPlayer) -> None:
    if not needs_backfill(player):
        return
    from .tasks import backfill_player_task

    try:
        transaction.on_commit(lambda: backfill_player_task.delay(player.pk))
    except Exception as exc:
        logger.warning("cannot queue faceit backfill for %s: %s", player.steam_id, exc)


def _lookup_allowed(user_key: str) -> bool:
    key = f"cs2:lookups:{user_key}"
    cache.add(key, 0, timeout=LOOKUP_WINDOW_SECONDS)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=LOOKUP_WINDOW_SECONDS)
        count = 1
    return count <= LOOKUP_LIMIT


def resolve_steam_id(query: str) -> str:
    kind, value = parse_player_query(query)
    if kind == "steam_id":
        return value
    if kind == "vanity":
        return resolve_vanity(value)
    if kind == "faceit":
        return steam_id_from_faceit_nickname(value)
    if kind == "text":
        # A bare word: Steam custom URL first, FACEIT nickname second.
        steam_id = ""
        if re.fullmatch(r"[\w\-.]{2,64}", value):
            try:
                steam_id = resolve_vanity(value)
            except Exception:
                steam_id = ""
            if not steam_id:
                steam_id = steam_id_from_faceit_nickname(value)
        return steam_id
    return ""


def lookup_player(query: str, *, user=None) -> LookupResult:
    try:
        steam_id = resolve_steam_id(query)
    except ValueError as exc:
        return LookupResult(False, str(exc))
    except Exception as exc:
        logger.info("player lookup %r: %s", query, exc)
        return LookupResult(False, "Steam или FACEIT сейчас не отвечают, попробуйте через минуту.")
    if not steam_id:
        return LookupResult(False, "Игрок не найден. Вставьте ссылку на профиль Steam, SteamID или ссылку FACEIT.")
    return open_player(steam_id, user=user)


def open_player(steam_id: str, *, user=None) -> LookupResult:
    """Finds or creates the tracked player and refreshes it when it is stale."""
    player = TrackedPlayer.objects.filter(steam_id=steam_id).first()
    created = player is None
    if created:
        user_key = str(getattr(user, "pk", None) or "anon")
        if not _lookup_allowed(user_key):
            return LookupResult(False, "Слишком много новых поисков, подождите несколько минут.")
        player = TrackedPlayer(steam_id=steam_id, added_by=user if getattr(user, "pk", None) else None)

    stale = player.last_synced_at is None or player.last_synced_at < timezone.now() - timedelta(hours=1)
    if created or stale:
        try:
            refresh_player(player)
        except PlayerNotFound:
            return LookupResult(False, "Такого профиля Steam нет.")
        except ValueError as exc:
            return LookupResult(False, str(exc))
        except Exception as exc:
            logger.info("player refresh %s: %s", steam_id, exc)
            if created:
                return LookupResult(False, "Steam сейчас не отвечает, попробуйте через минуту.")

    TrackedPlayer.objects.filter(pk=player.pk).update(
        lookup_count=player.lookup_count + 1, last_viewed_at=timezone.now()
    )
    player.lookup_count += 1
    schedule_backfill(player)
    return LookupResult(True, "", player, created)


def manual_refresh(player: TrackedPlayer) -> LookupResult:
    if player.last_synced_at and player.last_synced_at > timezone.now() - timedelta(minutes=REFRESH_COOLDOWN_MINUTES):
        return LookupResult(False, f"Статистика обновлялась меньше {REFRESH_COOLDOWN_MINUTES} минут назад.", player)
    try:
        refresh_player(player)
    except Exception as exc:
        logger.info("manual refresh %s: %s", player.steam_id, exc)
        return LookupResult(False, "Steam или FACEIT сейчас не отвечают, попробуйте позже.", player)
    schedule_backfill(player)
    return LookupResult(True, "Статистика обновлена.", player)


def enroll_caseup_users(limit: int = 25) -> int:
    """CaseUP users with a linked Steam account join the player base too."""
    from modules.v2.profiles.models import UserProfile

    tracked = TrackedPlayer.objects.values_list("steam_id", flat=True)
    steam_ids = list(
        UserProfile.objects.exclude(steam_id__isnull=True).exclude(steam_id="")
        .exclude(steam_id__in=tracked)
        .values_list("steam_id", flat=True)[:limit]
    )
    TrackedPlayer.objects.bulk_create(
        [TrackedPlayer(steam_id=steam_id) for steam_id in steam_ids if is_steam_id64(steam_id)],
        ignore_conflicts=True,
    )
    return len(steam_ids)


# ---------------------------------------------------------------------------
# Page context
# ---------------------------------------------------------------------------

def player_page_context(player: TrackedPlayer) -> dict:
    from modules.v2.profiles.models import UserProfile

    from .models import CS2_MAPS
    from .services import faceit_level_icon_url, faceit_level_progress

    def map_info(code: str) -> dict:
        return CS2_MAPS.get(code, {"name": code.replace("de_", "").title(), "color": "#4A5568", "color2": "#2D3748"})

    steam_maps = []
    for row in (player.steam_stats or {}).get("maps") or []:
        info = map_info(row.get("map_code", ""))
        steam_maps.append({**row, "map_name": info["name"], "color": info["color"], "color2": info["color2"]})
    faceit_maps = []
    for row in player.faceit_maps or []:
        info = map_info(row.get("map_code", ""))
        faceit_maps.append({**row, "map_name": info["name"], "color": info["color"], "color2": info["color2"]})

    matches = list(player.faceit_matches.all()[:20])
    caseup_profile = (
        UserProfile.objects.select_related("user").filter(steam_id=player.steam_id, user__is_active=True).first()
    )
    return {
        "player": player,
        "steam": player.steam_stats or {},
        "steam_maps": steam_maps,
        "faceit": player.faceit_lifetime or {},
        "faceit_maps": faceit_maps,
        "faceit_level_icon": faceit_level_icon_url(player.faceit_level),
        "faceit_progress": faceit_level_progress(player.faceit_elo, player.faceit_level),
        "recent_matches": matches,
        "stored_matches": player.faceit_matches.count(),
        "caseup_profile": caseup_profile,
        "backfill_running": needs_backfill(player),
    }
