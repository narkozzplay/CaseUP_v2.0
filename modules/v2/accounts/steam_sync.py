from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from django.conf import settings

from modules.v2.profiles.models import UserProfile

try:
    from social_django.models import UserSocialAuth
except Exception:  # pragma: no cover
    UserSocialAuth = None


def get_steam_social_auth(user) -> Optional["UserSocialAuth"]:
    if UserSocialAuth is None or user is None:
        return None
    return UserSocialAuth.objects.filter(user=user, provider="steam").first()


def extract_steam_payload(sa) -> dict:
    if not sa:
        return {}

    extra = sa.extra_data or {}
    player = extra.get("player") or {}

    steam_id = str(
        extra.get("steamid")
        or extra.get("id")
        or player.get("steamid")
        or sa.uid
        or ""
    ).strip()

    steam_personaname = (
        player.get("personaname")
        or extra.get("personaname")
        or extra.get("username")
        or ""
    ).strip()

    steam_avatar = (
        player.get("avatarfull")
        or extra.get("avatarfull")
        or player.get("avatar")
        or extra.get("avatar")
        or player.get("avatarmedium")
        or extra.get("avatarmedium")
        or ""
    ).strip()

    steam_profile_url = (
        player.get("profileurl")
        or extra.get("profileurl")
        or extra.get("profile_url")
        or ""
    ).strip()

    steam_profile_background = (
        player.get("profile_background")
        or extra.get("profile_background")
        or player.get("profile_background_image_url")
        or extra.get("profile_background_image_url")
        or ""
    ).strip()

    return {
        "steam_id": steam_id,
        "steam_personaname": steam_personaname,
        "steam_avatar": steam_avatar,
        "steam_profile_url": steam_profile_url,
        "steam_profile_background": steam_profile_background,
    }


def sync_profile_from_social_auth(user, *, profile: Optional[UserProfile] = None, save: bool = True) -> Optional[UserProfile]:
    if user is None:
        return profile

    sa = get_steam_social_auth(user)
    if not sa:
        return profile or UserProfile.objects.filter(user=user).first()

    payload = extract_steam_payload(sa)
    if not any(payload.values()):
        return profile or UserProfile.objects.filter(user=user).first()

    profile = profile or UserProfile.objects.filter(user=user).first()
    if profile is None:
        profile = UserProfile(user=user)

    changed_fields: list[str] = []

    for field, value in payload.items():
        if value and getattr(profile, field, "") != value:
            setattr(profile, field, value)
            changed_fields.append(field)

    if save and changed_fields:
        if profile.pk:
            profile.save(update_fields=changed_fields)
        else:
            profile.save()

    return profile


def fetch_steam_player_summary(*, steam_id: str, timeout: float = 10.0) -> dict:
    api_key = getattr(settings, "SOCIAL_AUTH_STEAM_API_KEY", "").strip()
    steam_id = (steam_id or "").strip()

    if not api_key or not steam_id:
        return {}

    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "steamids": steam_id,
            "format": "json",
        }
    )

    endpoints = [
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
        "https://partner.steam-api.com/ISteamUser/GetPlayerSummaries/v2/",
    ]
    headers = {
        "User-Agent": "CaseUP/steam-sync",
        "Accept-Language": "ru,en;q=0.9",
    }

    for endpoint in endpoints:
        try:
            request = urllib.request.Request(f"{endpoint}?{params}", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
        except Exception:
            continue

        players = payload.get("response", {}).get("players") or []
        if players:
            player = players[0]
            if isinstance(player, dict):
                return player

    return {}


def sync_profile_from_steam_api(profile: UserProfile, *, save: bool = True) -> UserProfile:
    if not profile or not profile.steam_id:
        return profile

    player = fetch_steam_player_summary(steam_id=profile.steam_id)
    if not player:
        return profile

    payload = {
        "steam_personaname": (player.get("personaname") or "").strip(),
        "steam_avatar": (
            player.get("avatarfull")
            or player.get("avatarmedium")
            or player.get("avatar")
            or ""
        ).strip(),
        "steam_profile_url": (player.get("profileurl") or "").strip(),
    }

    changed_fields: list[str] = []

    for field, value in payload.items():
        if value and getattr(profile, field, "") != value:
            setattr(profile, field, value)
            changed_fields.append(field)

    if save and changed_fields:
        profile.save(update_fields=changed_fields)

    return profile