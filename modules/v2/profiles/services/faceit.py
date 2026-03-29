from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from modules.v2.profiles.models import UserProfile


@dataclass
class FaceitSyncResult:
    ok: bool
    message: str
    payload: dict


def build_faceit_level_image_url(level: int) -> str:
    return f"https://cdn-frontend.faceit.com/web/960/src/app/assets/images-compress/skill-icons/skill_level_{level}_svg.svg"


def _save_faceit_error(profile: UserProfile, message: str) -> FaceitSyncResult:
    profile.faceit_player_id = ""
    profile.faceit_nickname = ""
    profile.faceit_avatar_url = ""
    profile.faceit_skill_level = 0
    profile.faceit_elo = 0
    profile.faceit_level_image_url = ""
    profile.faceit_last_sync_at = timezone.now()
    profile.faceit_last_error = message
    profile.save(
        update_fields=[
            "faceit_player_id",
            "faceit_nickname",
            "faceit_avatar_url",
            "faceit_skill_level",
            "faceit_elo",
            "faceit_level_image_url",
            "faceit_last_sync_at",
            "faceit_last_error",
        ]
    )
    return FaceitSyncResult(False, message, {})


def sync_faceit_profile(profile: UserProfile) -> FaceitSyncResult:
    api_key = getattr(settings, "FACEIT_API_KEY", "").strip()
    steam_id = (profile.steam_id or "").strip()

    if not api_key:
        return _save_faceit_error(profile, "FACEIT_API_KEY is not configured.")
    if not steam_id:
        return _save_faceit_error(profile, "Steam ID is missing.")

    query = urlencode({"game": "cs2", "game_player_id": steam_id})
    url = f"https://open.faceit.com/data/v4/players?{query}"
    request = Request(url, headers={"Authorization": f"Bearer {api_key}"})

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return _save_faceit_error(profile, f"FACEIT HTTP error: {exc.code}")
    except URLError as exc:
        return _save_faceit_error(profile, f"FACEIT network error: {exc.reason}")
    except Exception as exc:
        return _save_faceit_error(profile, f"FACEIT unexpected error: {exc}")

    games = payload.get("games") or {}
    game_data = games.get("cs2") or games.get("csgo") or {}
    if not game_data:
        return _save_faceit_error(profile, "FACEIT profile has no CS2 stats.")

    skill_level = int(game_data.get("skill_level") or 0)
    elo = int(float(game_data.get("faceit_elo") or 0))

    profile.faceit_player_id = payload.get("player_id", "") or ""
    profile.faceit_nickname = payload.get("nickname", "") or ""
    profile.faceit_avatar_url = payload.get("avatar", "") or ""
    profile.faceit_skill_level = skill_level
    profile.faceit_elo = elo
    profile.faceit_level_image_url = build_faceit_level_image_url(skill_level) if skill_level > 0 else ""
    profile.faceit_last_sync_at = timezone.now()
    profile.faceit_last_error = ""
    profile.save(
        update_fields=[
            "faceit_player_id",
            "faceit_nickname",
            "faceit_avatar_url",
            "faceit_skill_level",
            "faceit_elo",
            "faceit_level_image_url",
            "faceit_last_sync_at",
            "faceit_last_error",
        ]
    )

    return FaceitSyncResult(True, "FACEIT synced.", payload)


def sync_faceit_if_needed(profile: UserProfile, *, force: bool = False) -> FaceitSyncResult | None:
    if not profile.steam_id:
        return None

    if not force and profile.faceit_last_sync_at:
        if profile.faceit_last_sync_at > timezone.now() - timedelta(hours=12):
            return None

    return sync_faceit_profile(profile)