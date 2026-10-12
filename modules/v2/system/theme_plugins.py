from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)

GLOBAL_THEME_SCOPE = "global"
DASHBOARD_THEME_SCOPE = "dashboard"

_THEME_DISCOVERY_LOCK = threading.Lock()
_THEME_DISCOVERY_CACHE: dict[str, "RuntimeThemeManifest"] | None = None

_KNOWN_SCOPE_LABELS = {
    GLOBAL_THEME_SCOPE: "Global",
    DASHBOARD_THEME_SCOPE: "Dashboard",
    "steam_catalog": "Steam Catalog",
}

_DASHBOARD_NAMESPACES = {
    "frontend",
    "profiles",
    "system_config",
    "subscriptions",
    "farm",
    "tradehub",
    "steam_trading",
    "game_servers",
    "case_simulator",
    "celery_tasks",
    "tg_notifier",
    "store",
    "web_constructor",
}


@dataclass(frozen=True)
class RuntimeThemeManifest:
    key: str
    name: str
    description: str
    author: str
    scopes: tuple[str, ...]
    css_files: tuple[str, ...]
    template_overrides: dict[str, str]
    preview_image: str
    is_public: bool
    compatible_project_versions: tuple[str, ...]
    price_default: Decimal | None
    source_path: str


def _theme_plugins_root() -> Path:
    return Path(settings.BASE_DIR) / "modules" / "plugins" / "themes"


def _normalize_key(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _normalize_scope_values(raw_scope: object) -> tuple[str, ...]:
    if isinstance(raw_scope, str):
        candidates = [raw_scope]
    elif isinstance(raw_scope, (list, tuple, set)):
        candidates = list(raw_scope)
    else:
        candidates = []

    scopes: list[str] = []
    for item in candidates:
        normalized = _normalize_key(item)
        if not normalized:
            continue
        if normalized not in scopes:
            scopes.append(normalized)
    return tuple(scopes)


def _normalize_static_path(raw_path: object) -> str:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("/"):
        return ""
    if ".." in Path(normalized).parts:
        return ""
    return normalized


def _normalize_css_files(raw_css_files: object) -> tuple[str, ...]:
    if not isinstance(raw_css_files, (list, tuple, set)):
        return tuple()

    result: list[str] = []
    for item in raw_css_files:
        normalized = _normalize_static_path(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _normalize_template_overrides(raw_overrides: object) -> dict[str, str]:
    if not isinstance(raw_overrides, dict):
        return {}

    result: dict[str, str] = {}
    for key, value in raw_overrides.items():
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if not normalized_key or not normalized_value:
            continue
        result[normalized_key] = normalized_value
    return result


def _normalize_compatible_project_versions(raw_versions: object) -> tuple[str, ...]:
    if not isinstance(raw_versions, (list, tuple, set)):
        return tuple()

    versions: list[str] = []
    for value in raw_versions:
        normalized = str(value or "").strip()
        if normalized and normalized not in versions:
            versions.append(normalized)
    return tuple(versions)


def _normalize_price_default(raw_price: object) -> Decimal | None:
    if raw_price in (None, ""):
        return None
    try:
        value = Decimal(str(raw_price))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value.quantize(Decimal("0.01"))


def _parse_theme_manifest(theme_dir: Path) -> RuntimeThemeManifest | None:
    manifest_path = theme_dir / "theme.json"
    if not manifest_path.exists() or not manifest_path.is_file():
        return None

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        logger.warning("Skipping broken theme manifest: %s", manifest_path)
        return None

    key = _normalize_key(payload.get("key"))
    folder_key = _normalize_key(theme_dir.name)
    if not key or key != folder_key:
        logger.warning("Skipping theme %s: manifest key mismatch.", theme_dir)
        return None

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    author = str(payload.get("author") or "").strip()
    scopes = _normalize_scope_values(payload.get("scope"))
    css_files = _normalize_css_files(payload.get("css_files"))

    if not name or not description or not author or not scopes or not css_files:
        logger.warning("Skipping theme %s: required fields are missing.", theme_dir)
        return None

    return RuntimeThemeManifest(
        key=key,
        name=name,
        description=description,
        author=author,
        scopes=scopes,
        css_files=css_files,
        template_overrides=_normalize_template_overrides(payload.get("template_overrides")),
        preview_image=_normalize_static_path(payload.get("preview_image")),
        is_public=bool(payload.get("is_public", False)),
        compatible_project_versions=_normalize_compatible_project_versions(payload.get("compatible_project_versions")),
        price_default=_normalize_price_default(payload.get("price_default")),
        source_path=str(manifest_path),
    )


def discover_runtime_themes(*, refresh: bool = False) -> dict[str, RuntimeThemeManifest]:
    global _THEME_DISCOVERY_CACHE

    if _THEME_DISCOVERY_CACHE is not None and not refresh:
        return dict(_THEME_DISCOVERY_CACHE)

    with _THEME_DISCOVERY_LOCK:
        if _THEME_DISCOVERY_CACHE is not None and not refresh:
            return dict(_THEME_DISCOVERY_CACHE)

        root = _theme_plugins_root()
        discovered: dict[str, RuntimeThemeManifest] = {}
        if root.exists() and root.is_dir():
            for theme_dir in sorted(root.iterdir(), key=lambda item: item.name):
                if not theme_dir.is_dir():
                    continue
                manifest = _parse_theme_manifest(theme_dir)
                if manifest is None:
                    continue
                discovered[manifest.key] = manifest

        _THEME_DISCOVERY_CACHE = discovered
        return dict(discovered)


def get_runtime_theme(theme_key: str, *, refresh: bool = False) -> RuntimeThemeManifest | None:
    key = _normalize_key(theme_key)
    if not key:
        return None
    return discover_runtime_themes(refresh=refresh).get(key)


def is_runtime_theme_compatible(
    theme: RuntimeThemeManifest,
    *,
    project_version: object | None = None,
) -> bool:
    supported_versions = theme.compatible_project_versions
    if not supported_versions:
        return True

    version_value = ""
    if project_version is None:
        from .services import get_current_project_version

        current = get_current_project_version()
        version_value = str(getattr(current, "version", "") or "").strip()
    else:
        version_value = str(getattr(project_version, "version", project_version) or "").strip()

    if not version_value:
        return True
    return version_value in supported_versions


def get_theme_scope_label(scope: str) -> str:
    normalized = _normalize_key(scope)
    if not normalized:
        return "Scope"
    if normalized in _KNOWN_SCOPE_LABELS:
        return _KNOWN_SCOPE_LABELS[normalized]
    return normalized.replace("_", " ").title()


def resolve_request_theme_scope(request) -> str:
    namespace = str(getattr(getattr(request, "resolver_match", None), "namespace", "") or "").strip()
    if namespace == "steam_catalog":
        return "steam_catalog"
    if namespace in _DASHBOARD_NAMESPACES and getattr(request, "user", None) and request.user.is_authenticated:
        return DASHBOARD_THEME_SCOPE

    path = str(getattr(request, "path", "") or "")
    if "/dashboard-preview/" in path or "/control-center/" in path:
        return DASHBOARD_THEME_SCOPE
    return GLOBAL_THEME_SCOPE


def normalize_theme_preferences(raw_preferences: object) -> dict[str, str]:
    if not isinstance(raw_preferences, dict):
        return {}

    result: dict[str, str] = {}
    for raw_scope, raw_key in raw_preferences.items():
        scope = _normalize_key(raw_scope)
        key = _normalize_key(raw_key)
        if not scope or not key:
            continue
        result[scope] = key
    return result


def get_user_theme_preferences(user) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        return {}

    from modules.v2.profiles.models import UserProfile

    profile = (
        UserProfile.objects.filter(user_id=getattr(user, "id", None))
        .only("theme_preferences")
        .first()
    )
    if profile is None:
        return {}
    return normalize_theme_preferences(profile.theme_preferences)
