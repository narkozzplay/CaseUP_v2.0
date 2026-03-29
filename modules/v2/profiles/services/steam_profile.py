from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from modules.v2.profiles.models import UserProfile


_EXPLICIT_BACKGROUND_PATTERNS = [
    re.compile(
        r'class=["\'][^"\']*profile_page_background[^"\']*["\'][^>]*style=["\'][^"\']*background-image:\s*url\((?P<q>["\']?)(?P<url>(?:https?:)?//[^)"\']+)(?P=q)\)',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'class=["\'][^"\']*profile_header_bg_texture[^"\']*["\'][^>]*style=["\'][^"\']*background-image:\s*url\((?P<q>["\']?)(?P<url>(?:https?:)?//[^)"\']+)(?P=q)\)',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"profile_background"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+)"',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"profile_background_image"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+)"',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"profile_background_raw"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+)"',
        flags=re.IGNORECASE,
    ),
]

_EXPLICIT_BACKGROUND_VIDEO_PATTERNS = [
    re.compile(
        r'"profile_background_video"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+\.(?:webm|mp4)(?:\?[^"]*)?)"',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"profile_background_webm"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+\.webm(?:\?[^"]*)?)"',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"profile_background_mp4"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+\.mp4(?:\?[^"]*)?)"',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'"video"\s*:\s*"(?P<url>(?:https?:)?\\\\/\\\\/[^"]+\.(?:webm|mp4)(?:\?[^"]*)?)"',
        flags=re.IGNORECASE,
    ),
]

_IMAGE_URL_RE = re.compile(
    r'(?P<url>(?:https?:)?//[^"\'\s<>()]+(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\'\s<>()]*)?)',
    flags=re.IGNORECASE,
)

_VIDEO_URL_RE = re.compile(
    r'(?P<url>(?:https?:)?//[^"\'\s<>()]+\.(?:webm|mp4)(?:\?[^"\'\s<>()]*)?)',
    flags=re.IGNORECASE,
)

_GOOD_CONTEXT_TOKENS = (
    "profile_page_background",
    "profile_header_bg_texture",
    "profile_background",
    "profile_background_image",
    "profile_background_raw",
    "profile_background_video",
    "profile_background_webm",
    "profile_background_mp4",
    "profiletheme",
    "profile_theme",
)

_BAD_IMAGE_TOKENS = (
    "/avatars/",
    "avatarfull",
    "avatarmedium",
    "avatar",
    "icon",
    "badge",
    "capsule",
    "library_",
    "header.jpg",
    "header.png",
    "/public/images/apps/",
    "/apps/",
    "movie",
    "broadcast",
    "workshop",
    "screenshot",
)

_BAD_VIDEO_TOKENS = (
    "/movies/",
    "/trailers/",
    "/game_highlights/",
    "broadcast",
    "workshop",
)

_GOOD_URL_TOKENS = (
    "background",
    "profile",
    "steamstatic",
    "steamcdn",
    "store_item_assets",
    "communityitems",
    "public/images/items",
)

_ANIMATED_BACKGROUND_BLOCK_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*profile_animated_background[^"\']*["\'][^>]*>(?P<block>.*?)</div>',
    flags=re.IGNORECASE | re.DOTALL,
)

_SOURCE_TAG_VIDEO_RE = re.compile(
    r'<source[^>]+src=["\'](?P<url>(?:https?:)?//[^"\']+\.(?:webm|mp4)(?:\?[^"\']*)?)["\']',
    flags=re.IGNORECASE,
)


def _normalize_candidate_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""

    url = html.unescape(url)
    url = url.replace("\\/", "/")

    if url.startswith("//"):
        url = f"https:{url}"

    return url


def _context_score(text: str, start: int, end: int) -> int:
    left = max(0, start - 240)
    right = min(len(text), end + 240)
    context = text[left:right].lower()

    score = 0
    for token in _GOOD_CONTEXT_TOKENS:
        if token in context:
            score += 40

    return score


def _is_bad_image_url(url: str) -> bool:
    lower = url.lower()
    return any(token in lower for token in _BAD_IMAGE_TOKENS)


def _is_bad_video_url(url: str) -> bool:
    lower = url.lower()
    return any(token in lower for token in _BAD_VIDEO_TOKENS)


def _score_background_image_candidate(url: str) -> int:
    lower = url.lower()

    if _is_bad_image_url(url):
        return -1000

    score = 0
    for token in _GOOD_URL_TOKENS:
        if token in lower:
            score += 15

    if "profile_background" in lower:
        score += 50
    if "background" in lower:
        score += 20
    if "communityitems" in lower:
        score += 20
    if "store_item_assets" in lower:
        score += 15

    score += min(len(url), 220) // 10
    return score


def _score_background_video_candidate(url: str) -> int:
    lower = url.lower()

    if _is_bad_video_url(url):
        return -1000

    score = 0
    if "profile_background" in lower:
        score += 70
    if "background" in lower:
        score += 25
    if "communityitems" in lower:
        score += 25
    if "store_item_assets" in lower:
        score += 20
    if lower.endswith(".webm") or ".webm?" in lower:
        score += 10
    if lower.endswith(".mp4") or ".mp4?" in lower:
        score += 8

    return score


def extract_background_url(html_text: str) -> str:
    if not html_text:
        return ""

    text = html.unescape(html_text).replace("\\/", "/")

    for pattern in _EXPLICIT_BACKGROUND_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue

        candidate = _normalize_candidate_url(match.group("url"))
        if candidate and not _is_bad_image_url(candidate):
            return candidate

    candidates: list[tuple[int, str]] = []

    for match in _IMAGE_URL_RE.finditer(text):
        candidate = _normalize_candidate_url(match.group("url"))
        if not candidate or _is_bad_image_url(candidate):
            continue

        score = _score_background_image_candidate(candidate)
        score += _context_score(text, match.start(), match.end())

        if score < 60:
            continue

        candidates.append((score, candidate))

    if not candidates:
        return ""

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[0][1]

def _extract_profile_animated_background_sources(text: str) -> list[str]:
    if not text:
        return []

    collected: list[str] = []

    for block_match in _ANIMATED_BACKGROUND_BLOCK_RE.finditer(text):
        block = block_match.group("block") or ""
        for source_match in _SOURCE_TAG_VIDEO_RE.finditer(block):
            candidate = _normalize_candidate_url(source_match.group("url"))
            if not candidate or _is_bad_video_url(candidate):
                continue
            collected.append(candidate)

    if not collected:
        return []

    unique_urls: list[str] = []
    seen = set()

    for url in collected:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    unique_urls.sort(
        key=lambda value: (
            0 if ".webm" in value.lower() else 1,
            -_score_background_video_candidate(value),
        )
    )
    return unique_urls

def extract_background_video_url(html_text: str) -> str:
    if not html_text:
        return ""

    text = html.unescape(html_text).replace("\\/", "/")

    for pattern in _EXPLICIT_BACKGROUND_VIDEO_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue

        candidate = _normalize_candidate_url(match.group("url"))
        if candidate and not _is_bad_video_url(candidate):
            return candidate

    animated_sources = _extract_profile_animated_background_sources(text)
    if animated_sources:
        return animated_sources[0]

    candidates: list[tuple[int, str]] = []

    for match in _VIDEO_URL_RE.finditer(text):
        candidate = _normalize_candidate_url(match.group("url"))
        if not candidate or _is_bad_video_url(candidate):
            continue

        score = _score_background_video_candidate(candidate)
        score += _context_score(text, match.start(), match.end())

        if score < 80:
            continue

        candidates.append((score, candidate))

    if not candidates:
        return ""

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[0][1]


def _fetch_profile_html(profile_url: str, *, timeout: float = 10.0) -> str:
    url = (profile_url or "").strip()
    if not url:
        return ""

    headers = {
        "User-Agent": "Mozilla/5.0 (CaseUP) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        "Accept-Language": "ru,en;q=0.9",
    }
    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_steam_profile_background(profile_url: str, *, timeout: float = 10.0) -> str:
    html_text = _fetch_profile_html(profile_url, timeout=timeout)
    return extract_background_url(html_text)


def fetch_steam_profile_background_video(profile_url: str, *, timeout: float = 10.0) -> str:
    html_text = _fetch_profile_html(profile_url, timeout=timeout)
    return extract_background_video_url(html_text)


def fetch_steam_profile_media(profile_url: str, *, timeout: float = 10.0) -> tuple[str, str]:
    html_text = _fetch_profile_html(profile_url, timeout=timeout)
    return extract_background_url(html_text), extract_background_video_url(html_text)


def fetch_steam_level(steam_id: str, *, timeout: float = 10.0) -> int:
    api_key = getattr(settings, "SOCIAL_AUTH_STEAM_API_KEY", "").strip()
    if not api_key:
        return 0

    steam_id = (steam_id or "").strip()
    if not steam_id:
        return 0

    endpoints = [
        "https://partner.steam-api.com/IPlayerService/GetSteamLevel/v1/",
        "https://api.steampowered.com/IPlayerService/GetSteamLevel/v1/",
    ]

    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "steamid": steam_id,
            "format": "json",
        }
    )
    headers = {
        "User-Agent": "CaseUP/steam-profile",
        "Accept-Language": "ru,en;q=0.9",
    }

    for endpoint in endpoints:
        try:
            request = urllib.request.Request(f"{endpoint}?{params}", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
        except Exception:
            continue

        level = payload.get("response", {}).get("player_level")
        if isinstance(level, int) and level >= 0:
            return level

        if isinstance(level, str) and level.isdigit():
            return int(level)

    return 0


def sync_profile_background_if_needed(profile: UserProfile, *, force: bool = False) -> UserProfile:
    if not profile.steam_profile_url:
        return profile

    if not force and profile.steam_background_synced_at:
        if profile.steam_background_synced_at > timezone.now() - timedelta(hours=24):
            return profile

    try:
        background_url, background_video_url = fetch_steam_profile_media(profile.steam_profile_url)
    except Exception:
        return profile

    changed_fields: list[str] = []

    resolved_background_url = background_url or profile.steam_profile_background
    resolved_background_video_url = background_video_url or profile.steam_profile_background_video

    if resolved_background_url != profile.steam_profile_background:
        profile.steam_profile_background = resolved_background_url
        changed_fields.append("steam_profile_background")

    if resolved_background_video_url != profile.steam_profile_background_video:
        profile.steam_profile_background_video = resolved_background_video_url
        changed_fields.append("steam_profile_background_video")

    profile.steam_background_synced_at = timezone.now()
    changed_fields.append("steam_background_synced_at")

    if changed_fields:
        profile.save(update_fields=changed_fields)

    return profile


def sync_steam_level_if_needed(profile: UserProfile, *, force: bool = False) -> UserProfile:
    if not profile.steam_id:
        return profile

    if not force and profile.steam_level_synced_at:
        if profile.steam_level_synced_at > timezone.now() - timedelta(hours=24):
            return profile

    try:
        steam_level = fetch_steam_level(profile.steam_id)
    except Exception:
        return profile

    changed_fields: list[str] = []

    if steam_level != profile.steam_level:
        profile.steam_level = steam_level
        changed_fields.append("steam_level")

    profile.steam_level_synced_at = timezone.now()
    changed_fields.append("steam_level_synced_at")

    if changed_fields:
        profile.save(update_fields=changed_fields)

    return profile


def sync_profile_steam_meta_if_needed(profile: UserProfile, *, force: bool = False) -> UserProfile:
    profile = sync_profile_background_if_needed(profile, force=force)
    profile = sync_steam_level_if_needed(profile, force=force)
    return profile