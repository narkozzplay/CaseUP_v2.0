from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)

MODULE_REPLACE_ACTION = "replace"

_MODULE_DISCOVERY_LOCK = threading.Lock()
_MODULE_DISCOVERY_CACHE: dict[str, "RuntimeModuleManifest"] | None = None


@dataclass(frozen=True)
class RuntimeModulePlacement:
    slot: str
    action: str
    template: str


@dataclass(frozen=True)
class RuntimeModuleManifest:
    key: str
    name: str
    description: str
    author: str
    version: str
    target_scopes: tuple[str, ...]
    placements: tuple[RuntimeModulePlacement, ...]
    css_files: tuple[str, ...]
    js_files: tuple[str, ...]
    preview_image: str
    is_public: bool
    compatible_project_versions: tuple[str, ...]
    compatible_themes: tuple[str, ...]
    access_mode: str
    source_path: str


@dataclass(frozen=True)
class RuntimeModuleSlotState:
    slot: str
    requested_module_key: str
    module: RuntimeModuleManifest | None
    placement: RuntimeModulePlacement | None


def _module_plugins_root() -> Path:
    return Path(settings.BASE_DIR) / "modules" / "plugins" / "modules"


def _normalize_key(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _normalize_slot(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_safe_path(raw_path: object) -> str:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""

    as_path = Path(normalized)
    if as_path.is_absolute():
        return ""
    if normalized.startswith("/"):
        return ""
    if ".." in as_path.parts:
        return ""
    return normalized


def _normalize_string_tuple(raw_values: object, *, normalize_fn=None) -> tuple[str, ...]:
    if isinstance(raw_values, str):
        values = [raw_values]
    elif isinstance(raw_values, (list, tuple, set)):
        values = list(raw_values)
    else:
        values = []

    result: list[str] = []
    for value in values:
        normalized = normalize_fn(value) if normalize_fn else str(value or "").strip()
        if not normalized:
            continue
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _normalize_placements(raw_placements: object) -> tuple[RuntimeModulePlacement, ...]:
    if not isinstance(raw_placements, (list, tuple)):
        return tuple()

    result: list[RuntimeModulePlacement] = []
    for payload in raw_placements:
        if not isinstance(payload, dict):
            continue

        slot = _normalize_slot(payload.get("slot"))
        action = _normalize_key(payload.get("action"))
        template = _normalize_safe_path(payload.get("template"))

        if not slot or action != MODULE_REPLACE_ACTION or not template:
            continue

        result.append(
            RuntimeModulePlacement(
                slot=slot,
                action=MODULE_REPLACE_ACTION,
                template=template,
            )
        )
    return tuple(result)


def _parse_module_manifest(module_dir: Path) -> RuntimeModuleManifest | None:
    manifest_path = module_dir / "module.json"
    if not manifest_path.exists() or not manifest_path.is_file():
        return None

    try:
        # Accept both plain UTF-8 and UTF-8 with BOM manifests.
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        logger.warning("Skipping broken module manifest: %s", manifest_path)
        return None

    key = _normalize_key(payload.get("key"))
    folder_key = _normalize_key(module_dir.name)
    if not key or key != folder_key:
        logger.warning("Skipping module %s: manifest key mismatch.", module_dir)
        return None

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    author = str(payload.get("author") or "").strip()
    version = str(payload.get("version") or "").strip()
    target_scopes = _normalize_string_tuple(payload.get("target_scopes"), normalize_fn=_normalize_key)
    placements = _normalize_placements(payload.get("placements"))

    if not name or not description or not author or not version or not placements:
        logger.warning("Skipping module %s: required fields are missing.", module_dir)
        return None

    return RuntimeModuleManifest(
        key=key,
        name=name,
        description=description,
        author=author,
        version=version,
        target_scopes=target_scopes,
        placements=placements,
        css_files=_normalize_string_tuple(payload.get("css_files"), normalize_fn=_normalize_safe_path),
        js_files=_normalize_string_tuple(payload.get("js_files"), normalize_fn=_normalize_safe_path),
        preview_image=_normalize_safe_path(payload.get("preview_image")),
        is_public=bool(payload.get("is_public", False)),
        compatible_project_versions=_normalize_string_tuple(payload.get("compatible_project_versions")),
        compatible_themes=_normalize_string_tuple(payload.get("compatible_themes"), normalize_fn=_normalize_key),
        access_mode=str(payload.get("access_mode") or "").strip().lower(),
        source_path=str(manifest_path),
    )


def discover_runtime_modules(*, refresh: bool = False) -> dict[str, RuntimeModuleManifest]:
    global _MODULE_DISCOVERY_CACHE

    if _MODULE_DISCOVERY_CACHE is not None and not refresh:
        return dict(_MODULE_DISCOVERY_CACHE)

    with _MODULE_DISCOVERY_LOCK:
        if _MODULE_DISCOVERY_CACHE is not None and not refresh:
            return dict(_MODULE_DISCOVERY_CACHE)

        root = _module_plugins_root()
        discovered: dict[str, RuntimeModuleManifest] = {}
        if root.exists() and root.is_dir():
            for module_dir in sorted(root.iterdir(), key=lambda item: item.name):
                if not module_dir.is_dir():
                    continue
                manifest = _parse_module_manifest(module_dir)
                if manifest is None:
                    continue
                discovered[manifest.key] = manifest

        _MODULE_DISCOVERY_CACHE = discovered
        return dict(discovered)


def get_runtime_module(module_key: str, *, refresh: bool = False) -> RuntimeModuleManifest | None:
    key = _normalize_key(module_key)
    if not key:
        return None
    return discover_runtime_modules(refresh=refresh).get(key)


def is_runtime_module_compatible(
    module: RuntimeModuleManifest,
    *,
    project_version: object | None = None,
    active_theme_key: object | None = None,
) -> bool:
    supported_versions = module.compatible_project_versions
    supported_themes = module.compatible_themes

    version_value = ""
    if project_version is None:
        from .services import get_current_project_version

        current = get_current_project_version()
        version_value = str(getattr(current, "version", "") or "").strip()
    else:
        version_value = str(getattr(project_version, "version", project_version) or "").strip()

    if supported_versions and version_value and version_value not in supported_versions:
        return False

    if not supported_themes:
        return True
    if "*" in supported_themes:
        return True

    theme_key = ""
    if active_theme_key is None:
        from .services import get_active_theme

        active_theme = get_active_theme(project_version=project_version if project_version else None)
        theme_key = _normalize_key(getattr(active_theme, "key", ""))
    else:
        theme_key = _normalize_key(getattr(active_theme_key, "key", active_theme_key))

    if not theme_key:
        return True
    return theme_key in supported_themes


def normalize_module_preferences(raw_preferences: object) -> dict[str, str]:
    if not isinstance(raw_preferences, dict):
        return {}

    result: dict[str, str] = {}
    for raw_slot, raw_key in raw_preferences.items():
        slot = _normalize_slot(raw_slot)
        key = _normalize_key(raw_key)
        if not slot or not key:
            continue
        result[slot] = key
    return result


def get_user_module_preferences(user) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        return {}

    from modules.v2.profiles.models import UserProfile

    profile = (
        UserProfile.objects.filter(user_id=getattr(user, "id", None))
        .only("module_preferences")
        .first()
    )
    if profile is None:
        return {}
    return normalize_module_preferences(profile.module_preferences)


def get_runtime_module_slot_state(*, slot: str, module_key: str) -> RuntimeModuleSlotState:
    normalized_slot = _normalize_slot(slot)
    normalized_key = _normalize_key(module_key)
    if not normalized_slot or not normalized_key:
        return RuntimeModuleSlotState(
            slot=normalized_slot,
            requested_module_key=normalized_key,
            module=None,
            placement=None,
        )

    module = get_runtime_module(normalized_key)
    if module is None:
        return RuntimeModuleSlotState(
            slot=normalized_slot,
            requested_module_key=normalized_key,
            module=None,
            placement=None,
        )

    placement = next(
        (item for item in module.placements if item.slot == normalized_slot and item.action == MODULE_REPLACE_ACTION),
        None,
    )
    return RuntimeModuleSlotState(
        slot=normalized_slot,
        requested_module_key=normalized_key,
        module=module,
        placement=placement,
    )
