"""CS2 Match History sync service.

Flow:
  1. User saves their Steam Game Auth Code in CS2 stats settings.
  2. We call ICSGOPlayers_GetNextMatchSharingCode in a loop to walk the
     share-code chain starting from last_known_match_token.
  3. Each share code is decoded (base-57) to (match_id, outcome_id, token).
  4. We call ICSGOMatch_GetRoundStats/v2 to get match + player stats.
  5. Results are stored in Cs2MatchRecord / Cs2MatchPlayerStat.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.utils import timezone

from .models import CS2_MAPS, Cs2MatchPlayerStat, Cs2MatchRecord, Cs2MatchSyncConfig
from .services import _safe_int, _steam_api_key

# ---------------------------------------------------------------------------
# Share-code decoder (base-57 / CS2 share-code spec)
# ---------------------------------------------------------------------------

_SHARECODE_CHARS = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"


def decode_share_code(code: str) -> tuple[int, int, int]:
    """Decode a CSGO-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX share code.

    Returns (match_id, outcome_id, token_id).
    Raises ValueError on invalid code.
    """
    clean = code.replace("CSGO-", "").replace("-", "").strip()
    if not clean or len(clean) != 25:
        raise ValueError(f"Invalid share code length: {code!r}")

    n = 0
    for ch in reversed(clean):
        idx = _SHARECODE_CHARS.find(ch)
        if idx == -1:
            raise ValueError(f"Invalid character in share code: {ch!r}")
        n = n * 57 + idx

    # n encodes 18 little-endian bytes:
    # [0:8]  = match_id   (uint64 LE)
    # [8:16] = outcome_id (uint64 LE)
    # [16:18]= token      (uint16 LE)
    match_id = n & 0xFFFFFFFFFFFFFFFF
    n >>= 64
    outcome_id = n & 0xFFFFFFFFFFFFFFFF
    n >>= 64
    token = n & 0xFFFF

    return match_id, outcome_id, token


def encode_share_code(match_id: int, outcome_id: int, token: int) -> str:
    """Encode (match_id, outcome_id, token) back to a CSGO-... share code."""
    n = (token << 128) | (outcome_id << 64) | match_id
    chars = []
    for _ in range(25):
        chars.append(_SHARECODE_CHARS[n % 57])
        n //= 57
    s = "".join(reversed(chars))
    return "CSGO-" + "-".join(s[i:i+5] for i in range(0, 25, 5))


# ---------------------------------------------------------------------------
# Steam API helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
    except urllib.error.HTTPError as exc:
        # Re-raise with status attached so callers can distinguish 404 from real errors
        exc._http_status = exc.code  # type: ignore[attr-defined]
        raise


def _get_next_share_code(steam_id: str, auth_code: str, known_code: str) -> str | None:
    """Call ICSGOPlayers_GetNextMatchSharingCode.

    Returns the next share code string, or None if no new matches.
    Steam returns HTTP 404 when knownmatchid is the most recent match —
    that is NOT an error, just means no new matches available yet.
    Raises ValueError / other exceptions on genuine API errors.
    """
    api_key = _steam_api_key()
    if not api_key:
        raise ValueError("SOCIAL_AUTH_STEAM_API_KEY is not configured.")

    params = urllib.parse.urlencode({
        "key": api_key,
        "steamid": steam_id,
        "steamidkey": auth_code,
        "knownmatchid": known_code,
    })
    url = f"https://api.steampowered.com/ICSGOPlayers_GetNextMatchSharingCode/v1/?{params}"

    try:
        data = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # 404 = knownmatchid is already the latest match, no new ones
            return None
        raise

    result = data.get("result", {})
    status = _safe_int(result.get("status", 0))

    if status == 4:
        # No more matches available — normal "end of chain" response
        return None
    if status != 1:
        # Unexpected status: likely wrong auth code, Steam ID mismatch, or API issue
        raise ValueError(
            f"Steam API вернул неожиданный статус {status}. "
            "Проверьте правильность Game Auth Code и Steam ID."
        )

    next_code = (result.get("nextcode") or "").strip()
    return next_code if next_code and next_code != "n/a" else None


def _get_match_round_stats(match_id: int, outcome_id: int, token: int) -> dict[str, Any]:
    """Call ICSGOMatch_GetRoundStats/v2 and return parsed result dict.

    Returns empty dict on 404/403 (match not available or expired).
    Raises on network/server errors.
    """
    api_key = _steam_api_key()
    if not api_key:
        raise ValueError("SOCIAL_AUTH_STEAM_API_KEY is not configured.")

    params = urllib.parse.urlencode({
        "key": api_key,
        "match_id": match_id,
        "outcome_id": outcome_id,
        "token": token,
        "format": "json",
    })
    url = f"https://api.steampowered.com/ICSGOMatch_GetRoundStats/v2/?{params}"

    try:
        return _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            # Match data expired / not available — store what we have
            return {}
        raise


# ---------------------------------------------------------------------------
# Match data parser
# ---------------------------------------------------------------------------

def _parse_match_data(
    raw: dict,
    owner_steam_id: str,
) -> dict[str, Any] | None:
    """Extract match summary and player stats from GetRoundStats response.

    Returns a dict with keys: map_name, played_at, duration, score_own,
    score_opp, outcome, steam_ids, players.
    Returns None if the response doesn't contain usable data.
    """
    result = raw.get("result") or raw.get("roundstatsall", None)
    if not result:
        return None

    # The response can be either {"result": {...}} or {"roundstatsall": [...]}
    if isinstance(result, dict):
        match_data = result
        round_list = result.get("roundstatsall") or []
    else:
        round_list = result
        match_data = {}

    map_name: str = str(match_data.get("map") or "").strip()
    duration: int = _safe_int(match_data.get("duration"))
    timestamp: int = _safe_int(match_data.get("timestamp") or match_data.get("watchabletimestamp"))

    played_at = (
        datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)
        if timestamp > 0
        else None
    )

    # Steam IDs list (10 players for 5v5)
    steam_ids_raw = match_data.get("steamid") or []
    steam_ids = [str(sid) for sid in steam_ids_raw]

    # Use the last round's stats as final scoreline
    if not round_list:
        return {
            "map_name": map_name,
            "played_at": played_at,
            "duration": duration,
            "score_own": 0,
            "score_opp": 0,
            "outcome": Cs2MatchRecord.Outcome.UNKNOWN,
            "steam_ids": steam_ids,
            "players": [],
            "raw": raw,
        }

    last_round = round_list[-1]

    kills_list   = last_round.get("kills", []) or []
    deaths_list  = last_round.get("deaths", []) or []
    assists_list = last_round.get("assists", []) or []
    scores_list  = last_round.get("scores", []) or []
    mvps_list    = last_round.get("mvps", []) or []
    pings_list   = last_round.get("pings", []) or []
    hs_list      = last_round.get("headshots", []) or []
    team_scores  = last_round.get("team_scores", []) or []

    # Determine owner's team and outcome
    owner_idx = None
    for i, sid in enumerate(steam_ids):
        if str(sid) == str(owner_steam_id):
            owner_idx = i
            break

    score_own = 0
    score_opp = 0
    outcome = Cs2MatchRecord.Outcome.UNKNOWN

    if len(team_scores) >= 2:
        # Team 0 = first 5 players, team 1 = last 5 players
        if owner_idx is not None:
            own_team = 0 if owner_idx < 5 else 1
            opp_team = 1 - own_team
            score_own = _safe_int(team_scores[own_team])
            score_opp = _safe_int(team_scores[opp_team])
        else:
            score_own = _safe_int(team_scores[0])
            score_opp = _safe_int(team_scores[1])

        if score_own > score_opp:
            outcome = Cs2MatchRecord.Outcome.WIN
        elif score_own < score_opp:
            outcome = Cs2MatchRecord.Outcome.LOSS
        else:
            outcome = Cs2MatchRecord.Outcome.TIE

    # Build player list
    players = []
    for i, sid in enumerate(steam_ids):
        team_num = 2 if i < 5 else 3  # CS2 team numbering in proto
        players.append({
            "steam_id":      str(sid),
            "team":          team_num,
            "kills":         _safe_int(kills_list[i])   if i < len(kills_list)   else 0,
            "deaths":        _safe_int(deaths_list[i])  if i < len(deaths_list)  else 0,
            "assists":       _safe_int(assists_list[i]) if i < len(assists_list) else 0,
            "headshot_kills":_safe_int(hs_list[i])      if i < len(hs_list)      else 0,
            "score":         _safe_int(scores_list[i])  if i < len(scores_list)  else 0,
            "mvps":          _safe_int(mvps_list[i])    if i < len(mvps_list)    else 0,
            "ping":          _safe_int(pings_list[i])   if i < len(pings_list)   else 0,
        })

    return {
        "map_name": map_name,
        "played_at": played_at,
        "duration": duration,
        "score_own": score_own,
        "score_opp": score_opp,
        "outcome": outcome,
        "steam_ids": steam_ids,
        "players": players,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Sync pipeline
# ---------------------------------------------------------------------------

MAX_MATCHES_PER_SYNC = 20  # safety limit per sync call


@dataclass
class MatchSyncResult:
    ok: bool
    message: str
    imported: int = 0


def sync_cs2_match_history(user) -> MatchSyncResult:
    """Walk the share-code chain and import new matches for the user."""
    from modules.v2.profiles.models import UserProfile

    try:
        profile = user.profile_v2
    except UserProfile.DoesNotExist:
        return MatchSyncResult(False, "Profile not found.")

    steam_id = (getattr(profile, "steam_id", None) or "").strip()
    if not steam_id:
        return MatchSyncResult(False, "Steam ID not linked.")

    cfg, _ = Cs2MatchSyncConfig.objects.get_or_create(user=user)

    auth_code = cfg.auth_code.strip()
    if not auth_code:
        return MatchSyncResult(False, "Game Authentication Code not set.")

    known_token = cfg.last_known_match_token.strip()
    if not known_token:
        return MatchSyncResult(
            False,
            "Укажите токен последнего известного матча (Last Known Match Token) "
            "в настройках, чтобы начать с нужной точки.",
        )

    imported = 0
    current_code = known_token
    api_key = _steam_api_key()
    if not api_key:
        return MatchSyncResult(False, "Steam API key not configured.")

    try:
        for _ in range(MAX_MATCHES_PER_SYNC):
            next_code = _get_next_share_code(steam_id, auth_code, current_code)
            if not next_code:
                break  # no more new matches

            # Decode the share code
            try:
                match_id, outcome_id, token = decode_share_code(next_code)
            except ValueError:
                break

            # Skip already imported
            if Cs2MatchRecord.objects.filter(user=user, match_id=match_id).exists():
                current_code = next_code
                continue

            # Fetch detailed stats
            raw_data: dict = {}
            parsed: dict | None = None
            try:
                raw_data = _get_match_round_stats(match_id, outcome_id, token)
                parsed = _parse_match_data(raw_data, steam_id)
            except Exception:
                pass  # store the record with what we have

            if parsed is None:
                parsed = {
                    "map_name": "",
                    "played_at": None,
                    "duration": 0,
                    "score_own": 0,
                    "score_opp": 0,
                    "outcome": Cs2MatchRecord.Outcome.UNKNOWN,
                    "steam_ids": [],
                    "players": [],
                    "raw": raw_data,
                }

            record = Cs2MatchRecord.objects.create(
                user=user,
                match_id=match_id,
                share_code=next_code,
                map_name=parsed["map_name"],
                played_at=parsed["played_at"],
                duration_seconds=parsed["duration"],
                outcome=parsed["outcome"],
                score_own=parsed["score_own"],
                score_opp=parsed["score_opp"],
                raw_data=parsed["raw"],
            )

            # Create player stats rows
            player_rows = [
                Cs2MatchPlayerStat(
                    match=record,
                    steam_id=p["steam_id"],
                    team=p["team"],
                    kills=p["kills"],
                    deaths=p["deaths"],
                    assists=p["assists"],
                    headshot_kills=p["headshot_kills"],
                    score=p["score"],
                    mvps=p["mvps"],
                    ping=p["ping"],
                )
                for p in parsed["players"]
            ]
            if player_rows:
                Cs2MatchPlayerStat.objects.bulk_create(player_rows, ignore_conflicts=True)

            imported += 1
            current_code = next_code

    except Exception as exc:
        cfg.last_sync_error = str(exc)[:500]
        cfg.last_sync_at = timezone.now()
        cfg.save(update_fields=["last_sync_error", "last_sync_at", "updated_at"])
        return MatchSyncResult(False, f"Sync error: {exc}", imported=imported)

    # Save progress
    if imported > 0 or current_code != cfg.last_known_match_token:
        cfg.last_known_match_token = current_code
        cfg.matches_imported = Cs2MatchRecord.objects.filter(user=user).count()

    cfg.last_sync_at = timezone.now()
    cfg.last_sync_error = ""
    cfg.save(update_fields=[
        "last_known_match_token", "matches_imported",
        "last_sync_at", "last_sync_error", "updated_at",
    ])

    if imported == 0:
        return MatchSyncResult(True, "Новых матчей не найдено.", imported=0)
    return MatchSyncResult(True, f"Импортировано {imported} матч(ей).", imported=imported)


def save_auth_code(user, auth_code: str, last_known_token: str = "") -> None:
    """Persist the user's auth code and optional starting token."""
    cfg, _ = Cs2MatchSyncConfig.objects.get_or_create(user=user)
    cfg.auth_code = auth_code.strip()
    if last_known_token:
        cfg.last_known_match_token = last_known_token.strip()
    cfg.save(update_fields=["auth_code", "last_known_match_token", "updated_at"])
