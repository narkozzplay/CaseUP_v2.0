from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError, transaction
from django.template.loader import get_template
from django.template import TemplateDoesNotExist

from modules.v2.tg_notifier.models import TelegramNotifierConfig

from .module_registry import MODULE_REGISTRY, NAMESPACE_TO_MODULE_KEY, ModuleDefinition
from .models import (
    EnvironmentModuleState,
    ExternalServiceIntegration,
    ModuleCompatibility,
    ModuleRelease,
    ModuleState,
    ProjectVersion,
    SiteConfiguration,
    ThemeProfile,
)

ZERO = Decimal("0.00")
MONEY_Q = Decimal("0.01")
FALLBACK_PROJECT_VERSION = "0.1.38"
_BOOTSTRAP_VERSIONING_LOCK = threading.Lock()
_BOOTSTRAP_VERSIONING_DONE = False


@dataclass(frozen=True)
class ModuleStateView:
    key: str
    label: str
    description: str
    is_enabled: bool
    dependencies: tuple[str, ...]
    dependency_labels: tuple[str, ...]
    missing_dependencies: tuple[str, ...]
    missing_dependency_labels: tuple[str, ...]
    dependent_keys: tuple[str, ...]
    dependent_labels: tuple[str, ...]
    cascade_disable_labels: tuple[str, ...]
    addon_target_keys: tuple[str, ...]
    addon_target_labels: tuple[str, ...]
    addon_source_keys: tuple[str, ...]
    addon_source_labels: tuple[str, ...]
    can_enable: bool
    blocked_reason: str
    status_label: str


@dataclass(frozen=True)
class ModuleUpdatePreview:
    merged_state_map: dict[str, bool]
    direct_disabled_keys: tuple[str, ...]
    direct_disabled_labels: tuple[str, ...]
    cascaded_disabled_keys: tuple[str, ...]
    cascaded_disabled_labels: tuple[str, ...]
    changed_keys: tuple[str, ...]
    changed_labels: tuple[str, ...]

@dataclass(frozen=True)
class ModuleFlagReleaseInfo:
    module_key: str
    active_release_version: str
    active_release_channel: str
    runtime_release_version: str
    runtime_release_channel: str
    runtime_environment_label: str
    runtime_is_test_mode: bool


@dataclass(frozen=True)
class ExternalServiceHealth:
    key: str
    label: str
    status: str
    mode: str
    has_key: bool
    message: str


@dataclass(frozen=True)
class BulkCompatibilityUpdateSummary:
    updated: int
    skipped_invalid_range: int
    skipped_conflict: int
    skipped_unchanged: int


@dataclass(frozen=True)
class BulkReleaseCompatibilityEnsureSummary:
    created: int
    existing: int




def get_runtime_environment() -> str:
    explicit = getattr(settings, "CASEUP_RUNTIME_ENV", "").strip().lower()
    if explicit in {"dev", "prod", "test"}:
        return explicit
    module = (
        getattr(settings, "SETTINGS_MODULE", "")
        or getattr(settings, "DJANGO_SETTINGS_MODULE", "")
        or os.environ.get("DJANGO_SETTINGS_MODULE", "")
    )
    module = str(module).lower()
    if module.endswith(".prod"):
        return EnvironmentModuleState.Environment.PROD
    if module.endswith(".testing"):
        return EnvironmentModuleState.Environment.TEST
    return EnvironmentModuleState.Environment.DEV


def get_project_build_version() -> str:
    version_path = Path(settings.BASE_DIR) / "VERSION.txt"
    try:
        raw_text = version_path.read_text(encoding="utf-8-sig")
    except OSError:
        return FALLBACK_PROJECT_VERSION
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", raw_text)
    if not match:
        return FALLBACK_PROJECT_VERSION
    return match.group(1)


def _build_project_version_order(version: str) -> int:
    parts = [int(part) for part in re.findall(r"\d+", version)]
    if not parts:
        return 1
    if len(parts) > 1 and parts[0] == 0:
        parts = parts[1:]
    order = int("".join(str(part) for part in parts))
    return max(order, 1)


def _next_project_version_order(base_order: int) -> int:
    version_order = max(int(base_order or 1), 1)
    while ProjectVersion.objects.filter(version_order=version_order).exists():
        version_order += 1
    return version_order


def get_site_configuration() -> SiteConfiguration:
    config = SiteConfiguration.objects.filter(singleton_key=1).first()
    if config is not None:
        return config
    config, _created = SiteConfiguration.objects.get_or_create(
        singleton_key=1,
        defaults=SiteConfiguration.bootstrap_defaults(),
    )
    return config


def get_external_service_integration() -> ExternalServiceIntegration:
    integration = ExternalServiceIntegration.objects.filter(singleton_key=1).first()
    if integration is not None:
        return integration
    integration, _created = ExternalServiceIntegration.objects.get_or_create(singleton_key=1)
    return integration


def save_external_service_integration(*, payload: dict[str, object]) -> ExternalServiceIntegration:
    integration = get_external_service_integration()
    health_fields = {"steam_health_mode", "faceit_health_mode", "telegram_health_mode"}
    for field in (
        "steam_api_key",
        "faceit_api_key",
        "telegram_bot_token",
        "steam_health_mode",
        "faceit_health_mode",
        "telegram_health_mode",
        "degraded_stub_message",
        "down_stub_message",
    ):
        if field in payload:
            if field in health_fields and not payload[field]:
                continue
            setattr(integration, field, payload[field])
    integration.save()
    return integration


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


def get_masked_integration_secrets() -> dict[str, str]:
    integration = get_external_service_integration()
    return {
        "steam_api_key": _mask_secret(integration.steam_api_key),
        "faceit_api_key": _mask_secret(integration.faceit_api_key),
        "telegram_bot_token": _mask_secret(integration.telegram_bot_token),
    }


def get_external_service_health() -> list[ExternalServiceHealth]:
    integration = get_external_service_integration()

    forced = {
        ExternalServiceIntegration.HealthMode.FORCE_UP: ("up", "Принудительно отмечен как рабочий."),
        ExternalServiceIntegration.HealthMode.FORCE_DEGRADED: ("degraded", integration.degraded_stub_message),
        ExternalServiceIntegration.HealthMode.FORCE_DOWN: ("down", integration.down_stub_message),
    }

    checks = [
        ("steam", "Steam API", integration.steam_api_key, integration.steam_health_mode, _check_steam),
        ("faceit", "FaceIT API", integration.faceit_api_key, integration.faceit_health_mode, _check_faceit),
        ("telegram", "Telegram Bot API", integration.telegram_bot_token, integration.telegram_health_mode, _check_telegram),
    ]
    results: list[ExternalServiceHealth] = []
    for key, label, secret, mode, checker in checks:
        has_key = bool(secret)
        if mode in forced:
            status, message = forced[mode]
        elif not has_key:
            status, message = "missing_key", "API ключ не задан."
        else:
            status, message = checker(secret)
        results.append(ExternalServiceHealth(key=key, label=label, status=status, mode=mode, has_key=has_key, message=message))
    return results


def _check_steam(api_key: str) -> tuple[str, str]:
    try:
        response = requests.get(
            "https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/",
            params={"key": api_key},
            timeout=4,
        )
    except requests.RequestException as exc:
        return ("down", f"Steam API недоступен: {exc.__class__.__name__}.")
    if response.ok:
        return ("up", "Steam API отвечает корректно.")
    return ("degraded", f"Steam API вернул HTTP {response.status_code}.")


def _check_faceit(api_key: str) -> tuple[str, str]:
    try:
        response = requests.get(
            "https://open.faceit.com/data/v4/games",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=4,
        )
    except requests.RequestException as exc:
        return ("down", f"FaceIT API недоступен: {exc.__class__.__name__}.")
    if response.ok:
        return ("up", "FaceIT API отвечает корректно.")
    if response.status_code in {401, 403}:
        return ("degraded", "FaceIT API отклонил ключ (401/403).")
    return ("degraded", f"FaceIT API вернул HTTP {response.status_code}.")


def _check_telegram(bot_token: str) -> tuple[str, str]:
    try:
        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=4)
    except requests.RequestException as exc:
        return ("down", f"Telegram API недоступен: {exc.__class__.__name__}.")
    if not response.ok:
        return ("degraded", f"Telegram API вернул HTTP {response.status_code}.")
    payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
    if payload.get("ok"):
        return ("up", "Telegram bot token валиден.")
    return ("degraded", "Telegram API вернул отрицательный ответ на getMe.")


def get_current_project_version() -> ProjectVersion | None:
    config = get_site_configuration()
    if config.current_project_version_id:
        return config.current_project_version
    runtime_env = get_runtime_environment()
    if runtime_env == EnvironmentModuleState.Environment.PROD:
        current = ProjectVersion.objects.filter(is_default_for_prod=True).first()
        if current:
            return current
    if runtime_env == EnvironmentModuleState.Environment.DEV:
        current = ProjectVersion.objects.filter(is_default_for_dev=True).first()
        if current:
            return current
    return ProjectVersion.objects.filter(is_current=True).first() or ProjectVersion.objects.order_by("version_order").last()


@transaction.atomic
def ensure_module_states() -> dict[str, ModuleState]:
    existing = {row.key: row for row in ModuleState.objects.select_for_update().all()}

    for key, definition in MODULE_REGISTRY.items():
        if key not in existing:
            default_enabled = definition.default_enabled
            if key == "tg_notifier":
                default_enabled = bool(getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False))
            existing[key] = ModuleState.objects.create(key=key, is_enabled=default_enabled)

    stale_keys = set(existing) - set(MODULE_REGISTRY)
    if stale_keys:
        ModuleState.objects.filter(key__in=stale_keys).delete()
        existing = {row.key: row for row in ModuleState.objects.select_for_update().all()}

    return existing


@transaction.atomic
def ensure_environment_module_states() -> None:
    releases_by_key: dict[str, ModuleRelease] = {}
    for row in ModuleRelease.objects.filter(is_active=True).order_by("module_key", "-is_default", "-version_order"):
        releases_by_key.setdefault(row.module_key, row)
    for environment in EnvironmentModuleState.Environment.values:
        for key, definition in MODULE_REGISTRY.items():
            state, created = EnvironmentModuleState.objects.get_or_create(
                environment=environment,
                module_key=key,
                defaults={
                    "is_enabled": definition.default_enabled,
                    "module_release": releases_by_key.get(key),
                    "is_test_mode": environment == EnvironmentModuleState.Environment.TEST,
                },
            )
            if created:
                continue
            if state.module_release_id is None and releases_by_key.get(key):
                state.module_release = releases_by_key[key]
                state.save(update_fields=["module_release", "updated_at"])


@transaction.atomic
def ensure_project_versions() -> None:
    config = get_site_configuration()
    version_value = get_project_build_version()
    version = ProjectVersion.objects.select_for_update().filter(version=version_value).first()
    if version is None:
        version = ProjectVersion.objects.create(
            version=version_value,
            version_order=_next_project_version_order(_build_project_version_order(version_value)),
            title=f"CaseUP {version_value}",
            status=ProjectVersion.Status.ACTIVE,
        )

    ProjectVersion.objects.exclude(pk=version.pk).update(
        is_current=False,
        is_default_for_dev=False,
        is_default_for_prod=False,
    )

    changed_fields: list[str] = []
    if version.title != f"CaseUP {version_value}":
        version.title = f"CaseUP {version_value}"
        changed_fields.append("title")
    if version.status != ProjectVersion.Status.ACTIVE:
        version.status = ProjectVersion.Status.ACTIVE
        changed_fields.append("status")
    if not version.is_current:
        version.is_current = True
        changed_fields.append("is_current")
    if not version.is_default_for_dev:
        version.is_default_for_dev = True
        changed_fields.append("is_default_for_dev")
    if not version.is_default_for_prod:
        version.is_default_for_prod = True
        changed_fields.append("is_default_for_prod")
    if changed_fields:
        version.save(update_fields=[*changed_fields, "updated_at"])

    config.current_project_version = version
    config.save(update_fields=["current_project_version", "updated_at"])


@transaction.atomic
def ensure_themes() -> None:
    version = get_current_project_version()
    theme_specs = [
        {
            "key": "default",
            "defaults": {
                "name": "Default CaseUP",
                "code": "default",
                "description": "Текущий основной стиль проекта.",
                "template_base_path": "themes/default",
                "css_file": "css/themes/default.css",
                "is_default": True,
                "is_active": True,
                "is_test_only": False,
                "project_version_from": version,
                "project_version_to": version,
            },
        },
        {
            "key": "midnight",
            "defaults": {
                "name": "Midnight Neon",
                "code": "midnight",
                "description": "Альтернативная тёмная тема для dev/test и plugin-поставок.",
                "template_base_path": "themes/midnight",
                "css_file": "css/themes/midnight.css",
                "is_default": False,
                "is_active": True,
                "is_test_only": True,
                "project_version_from": version,
                "project_version_to": version,
            },
        },
    ]

    created_by_key: dict[str, ThemeProfile] = {}
    for spec in theme_specs:
        theme, _created = ThemeProfile.objects.update_or_create(key=spec["key"], defaults=spec["defaults"])
        created_by_key[spec["key"]] = theme

    config = get_site_configuration()
    if not config.active_theme_id:
        config.active_theme = created_by_key["default"]
        config.save(update_fields=["active_theme", "updated_at"])


@transaction.atomic
def ensure_module_releases() -> None:
    version = get_current_project_version()
    seeded_version = version.version if version else FALLBACK_PROJECT_VERSION
    seeded_order = version.version_order if version else _build_project_version_order(FALLBACK_PROJECT_VERSION)
    for key, definition in MODULE_REGISTRY.items():
        release, _created = ModuleRelease.objects.get_or_create(
            module_key=key,
            version=seeded_version,
            defaults={
                "version_order": seeded_order,
                "channel": ModuleRelease.Channel.STABLE,
                "title": f"{definition.label} {seeded_version}",
                "description": definition.description,
                "is_default": False,
                "is_active": True,
            },
        )
        changed = False
        if not release.is_active:
            release.is_active = True
            changed = True
        ModuleRelease.objects.exclude(pk=release.pk).filter(module_key=key, is_default=True).update(is_default=False)
        if not release.is_default:
            release.is_default = True
            changed = True
        if changed:
            release.save(update_fields=["is_active", "is_default", "updated_at"])
        if version and not ModuleCompatibility.objects.filter(
            module_release=release,
            project_version_from=version,
            project_version_to=version,
        ).exists():
            ModuleCompatibility.objects.create(
                module_release=release,
                project_version_from=version,
                project_version_to=version,
                compatibility_status=ModuleCompatibility.CompatibilityStatus.SUPPORTED,
                notes="Seeded from current module registry.",
            )


@transaction.atomic
def bulk_update_module_compatibility_to_target_version(
    *,
    compatibilities,
    target_version: ProjectVersion,
) -> BulkCompatibilityUpdateSummary:
    updated = 0
    skipped_invalid_range = 0
    skipped_conflict = 0
    skipped_unchanged = 0

    target_version_id = target_version.id
    if not target_version_id:
        return BulkCompatibilityUpdateSummary(
            updated=0,
            skipped_invalid_range=0,
            skipped_conflict=0,
            skipped_unchanged=0,
        )

    compatibility_rows = list(
        compatibilities.select_for_update().select_related("module_release", "project_version_from", "project_version_to")
    )
    for compatibility in compatibility_rows:
        if compatibility.project_version_from.version_order > target_version.version_order:
            skipped_invalid_range += 1
            continue

        if compatibility.project_version_to_id == target_version_id:
            skipped_unchanged += 1
            continue

        has_conflict = ModuleCompatibility.objects.filter(
            module_release=compatibility.module_release,
            project_version_from=compatibility.project_version_from,
            project_version_to=target_version,
        ).exclude(pk=compatibility.pk).exists()
        if has_conflict:
            skipped_conflict += 1
            continue

        compatibility.project_version_to = target_version
        compatibility.save(update_fields=["project_version_to", "updated_at"])
        updated += 1

    return BulkCompatibilityUpdateSummary(
        updated=updated,
        skipped_invalid_range=skipped_invalid_range,
        skipped_conflict=skipped_conflict,
        skipped_unchanged=skipped_unchanged,
    )


@transaction.atomic
def bulk_ensure_module_releases_compatibility_for_target_version(
    *,
    module_releases,
    target_version: ProjectVersion,
) -> BulkReleaseCompatibilityEnsureSummary:
    created = 0
    existing = 0

    if not target_version.id:
        return BulkReleaseCompatibilityEnsureSummary(created=0, existing=0)

    release_rows = list(module_releases.select_for_update())
    for release in release_rows:
        _compatibility, is_created = ModuleCompatibility.objects.get_or_create(
            module_release=release,
            project_version_from=target_version,
            project_version_to=target_version,
            defaults={
                "compatibility_status": ModuleCompatibility.CompatibilityStatus.SUPPORTED,
                "notes": "Bulk ensured from admin action.",
            },
        )
        if is_created:
            created += 1
        else:
            existing += 1

    return BulkReleaseCompatibilityEnsureSummary(created=created, existing=existing)


def bootstrap_versioning() -> None:
    ensure_project_versions()
    ensure_module_releases()
    ensure_themes()
    ensure_environment_module_states()


def bootstrap_versioning_once() -> None:
    global _BOOTSTRAP_VERSIONING_DONE

    if _BOOTSTRAP_VERSIONING_DONE and ProjectVersion.objects.only("id").exists():
        return

    with _BOOTSTRAP_VERSIONING_LOCK:
        if _BOOTSTRAP_VERSIONING_DONE and ProjectVersion.objects.only("id").exists():
            return
        bootstrap_versioning()
        _BOOTSTRAP_VERSIONING_DONE = True


def get_module_state_map(environment: str | None = None) -> dict[str, bool]:
    environment = environment or get_runtime_environment()
    if EnvironmentModuleState.objects.exists():
        state_map = {key: definition.default_enabled for key, definition in MODULE_REGISTRY.items()}
        persisted = EnvironmentModuleState.objects.filter(environment=environment).values_list("module_key", "is_enabled")
        for key, is_enabled in persisted:
            state_map[key] = is_enabled
        return state_map
    state_map = {key: definition.default_enabled for key, definition in MODULE_REGISTRY.items()}
    persisted = ModuleState.objects.filter(key__in=MODULE_REGISTRY).values_list("key", "is_enabled")
    for key, is_enabled in persisted:
        state_map[key] = is_enabled
    return state_map


def _get_dependents_map() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}
    for key, definition in MODULE_REGISTRY.items():
        for dependency_key in definition.dependencies:
            mapping.setdefault(dependency_key, []).append(key)
    return {key: tuple(values) for key, values in mapping.items()}


def _get_addon_maps() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    source_to_targets: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}
    target_to_sources: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}
    for key, definition in MODULE_REGISTRY.items():
        for target_key in definition.addon_targets:
            if target_key not in MODULE_REGISTRY:
                continue
            source_to_targets.setdefault(key, []).append(target_key)
            target_to_sources.setdefault(target_key, []).append(key)
    return ({key: tuple(values) for key, values in source_to_targets.items()}, {key: tuple(values) for key, values in target_to_sources.items()})


def get_module_state_views(state_map: dict[str, bool] | None = None) -> list[ModuleStateView]:
    state_map = state_map or get_module_state_map()
    dependents_map = _get_dependents_map()
    addon_targets_map, addon_sources_map = _get_addon_maps()
    rows: list[ModuleStateView] = []
    for key, definition in MODULE_REGISTRY.items():
        is_enabled = state_map.get(key, definition.default_enabled)
        missing_dependencies = tuple(dep for dep in definition.dependencies if not state_map.get(dep, False))
        dependent_keys = dependents_map.get(key, ())
        cascade_disable_labels = tuple(MODULE_REGISTRY[dep].label for dep in dependent_keys if state_map.get(dep, MODULE_REGISTRY[dep].default_enabled))
        status_label = "Enabled" if is_enabled and not missing_dependencies else "Conflict" if is_enabled else "Blocked" if missing_dependencies else "Disabled"
        missing_dependency_labels = tuple(MODULE_REGISTRY[item].label for item in missing_dependencies)
        blocked_reason = "Нельзя включить, пока выключены зависимости: " + ", ".join(missing_dependency_labels) + "." if missing_dependency_labels else ""
        rows.append(ModuleStateView(
            key=key,
            label=definition.label,
            description=definition.description,
            is_enabled=is_enabled,
            dependencies=definition.dependencies,
            dependency_labels=tuple(MODULE_REGISTRY[item].label for item in definition.dependencies),
            missing_dependencies=missing_dependencies,
            missing_dependency_labels=missing_dependency_labels,
            dependent_keys=dependent_keys,
            dependent_labels=tuple(MODULE_REGISTRY[item].label for item in dependent_keys),
            cascade_disable_labels=cascade_disable_labels,
            addon_target_keys=addon_targets_map.get(key, ()),
            addon_target_labels=tuple(MODULE_REGISTRY[item].label for item in addon_targets_map.get(key, ())),
            addon_source_keys=addon_sources_map.get(key, ()),
            addon_source_labels=tuple(MODULE_REGISTRY[item].label for item in addon_sources_map.get(key, ())),
            can_enable=not bool(missing_dependencies),
            blocked_reason=blocked_reason,
            status_label=status_label,
        ))
    return rows


def validate_module_state_map(proposed: dict[str, bool]) -> None:
    unknown = set(proposed) - set(MODULE_REGISTRY)
    if unknown:
        raise ValidationError(f"Неизвестные ключи модулей: {', '.join(sorted(unknown))}")
    for key, definition in MODULE_REGISTRY.items():
        if not proposed.get(key, definition.default_enabled):
            continue
        missing = [dep for dep in definition.dependencies if not proposed.get(dep, False)]
        if missing:
            dep_labels = ", ".join(MODULE_REGISTRY[item].label for item in missing)
            raise ValidationError(f"Нельзя включить модуль '{definition.label}', пока выключены зависимости: {dep_labels}.")


def build_effective_module_state_map(current_state_map: dict[str, bool], proposed: dict[str, bool]) -> dict[str, bool]:
    normalized_proposed = {key: bool(value) for key, value in proposed.items()}
    merged = {key: current_state_map.get(key, definition.default_enabled) for key, definition in MODULE_REGISTRY.items()}
    merged.update(normalized_proposed)
    changed = True
    while changed:
        changed = False
        for key, definition in MODULE_REGISTRY.items():
            if not merged.get(key, definition.default_enabled):
                continue
            missing = [dep for dep in definition.dependencies if not merged.get(dep, False)]
            if not missing:
                continue
            was_enabled_before = current_state_map.get(key, definition.default_enabled)
            explicitly_enabled_now = bool(normalized_proposed.get(key, was_enabled_before))
            if not was_enabled_before and explicitly_enabled_now:
                dep_labels = ", ".join(MODULE_REGISTRY[item].label for item in missing)
                raise ValidationError(f"Нельзя включить модуль '{definition.label}', пока выключены зависимости: {dep_labels}.")
            merged[key] = False
            changed = True
    validate_module_state_map(merged)
    return merged


def preview_module_update(current_state_map: dict[str, bool], proposed: dict[str, bool]) -> ModuleUpdatePreview:
    merged = build_effective_module_state_map(current_state_map, proposed)
    direct_disabled_keys = tuple(key for key, value in proposed.items() if not bool(value) and current_state_map.get(key, MODULE_REGISTRY[key].default_enabled))
    cascaded_disabled_keys = tuple(key for key in MODULE_REGISTRY if current_state_map.get(key, MODULE_REGISTRY[key].default_enabled) and not merged.get(key, MODULE_REGISTRY[key].default_enabled) and key not in direct_disabled_keys)
    changed_keys = tuple(key for key in MODULE_REGISTRY if current_state_map.get(key, MODULE_REGISTRY[key].default_enabled) != merged.get(key, MODULE_REGISTRY[key].default_enabled))
    return ModuleUpdatePreview(
        merged_state_map=merged,
        direct_disabled_keys=direct_disabled_keys,
        direct_disabled_labels=tuple(MODULE_REGISTRY[key].label for key in direct_disabled_keys),
        cascaded_disabled_keys=cascaded_disabled_keys,
        cascaded_disabled_labels=tuple(MODULE_REGISTRY[key].label for key in cascaded_disabled_keys),
        changed_keys=changed_keys,
        changed_labels=tuple(MODULE_REGISTRY[key].label for key in changed_keys),
    )


def get_compatible_module_release(module_key: str, *, project_version: ProjectVersion | None = None, environment: str | None = None) -> ModuleRelease | None:
    project_version = project_version or get_current_project_version()
    environment = environment or get_runtime_environment()
    state = EnvironmentModuleState.objects.filter(environment=environment, module_key=module_key).select_related("module_release").first()
    if state and state.module_release_id:
        release = state.module_release
        if release.channel == ModuleRelease.Channel.TEST and environment == EnvironmentModuleState.Environment.PROD:
            return None
        return release
    releases = ModuleRelease.objects.filter(module_key=module_key, is_active=True).order_by("-is_default", "-version_order")
    if project_version is None:
        return releases.first()
    compat_qs = ModuleCompatibility.objects.filter(
        module_release__module_key=module_key,
        project_version_from__version_order__lte=project_version.version_order,
        project_version_to__version_order__gte=project_version.version_order,
    ).select_related("module_release", "project_version_from", "project_version_to").order_by("-module_release__is_default", "-module_release__version_order")
    for compat in compat_qs:
        if compat.compatibility_status == ModuleCompatibility.CompatibilityStatus.UNSUPPORTED:
            continue
        if compat.compatibility_status == ModuleCompatibility.CompatibilityStatus.TEST_ONLY and environment == EnvironmentModuleState.Environment.PROD:
            continue
        if compat.module_release.channel == ModuleRelease.Channel.TEST and environment == EnvironmentModuleState.Environment.PROD:
            continue
        return compat.module_release
    return releases.first()


def get_module_flag_release_map(*, project_version: ProjectVersion | None = None, environment: str | None = None) -> dict[str, ModuleFlagReleaseInfo]:
    project_version = project_version or get_current_project_version()
    environment = environment or get_runtime_environment()
    env_states = {
        state.module_key: state
        for state in EnvironmentModuleState.objects.filter(environment=environment).select_related("module_release")
    }
    result: dict[str, ModuleFlagReleaseInfo] = {}
    for key in MODULE_REGISTRY:
        active_release = get_compatible_module_release(key, project_version=project_version, environment=environment)
        runtime_state = env_states.get(key)
        runtime_release = runtime_state.module_release if runtime_state and runtime_state.module_release_id else None
        result[key] = ModuleFlagReleaseInfo(
            module_key=key,
            active_release_version=active_release.version if active_release else "—",
            active_release_channel=active_release.channel if active_release else "—",
            runtime_release_version=runtime_release.version if runtime_release else "—",
            runtime_release_channel=runtime_release.channel if runtime_release else "—",
            runtime_environment_label=environment.upper(),
            runtime_is_test_mode=bool(runtime_state.is_test_mode) if runtime_state else False,
        )
    return result


def _theme_shell_template_signature(theme: ThemeProfile | None) -> tuple[bool, ...]:
    if not theme or not theme.template_base_path:
        return (False, False, False)
    return tuple(
        _template_exists(f"{theme.template_base_path}/{relative_path}")
        for relative_path in (
            "layouts/base_dashboard.html",
            "components/sidebar_dashboard.html",
            "components/dashboard_content_frame.html",
        )
    )


def build_theme_update_headers(*, previous_theme: ThemeProfile | None, next_theme: ThemeProfile | None) -> dict[str, str]:
    from django.templatetags.static import static

    next_css_url = static(next_theme.css_file) if next_theme and next_theme.css_file else ""
    theme_mode = "css"
    if _theme_shell_template_signature(previous_theme) != _theme_shell_template_signature(next_theme):
        theme_mode = "reload"

    return {
        "X-Dashboard-Theme-Mode": theme_mode,
        "X-Dashboard-Theme-Css-Url": next_css_url,
        "X-Dashboard-Theme-Key": next_theme.key if next_theme else "",
    }


def get_module_release_matrix(*, project_version: ProjectVersion | None = None) -> list[dict[str, object]]:
    project_version = project_version or get_current_project_version()
    rows: list[dict[str, object]] = []
    for key, definition in MODULE_REGISTRY.items():
        compatibilities = list(
            ModuleCompatibility.objects.filter(module_release__module_key=key).select_related(
                "module_release", "project_version_from", "project_version_to"
            ).order_by("-module_release__version_order", "project_version_from__version_order")
        )
        env_states = {
            state.environment: state
            for state in EnvironmentModuleState.objects.filter(module_key=key).select_related("module_release")
        }
        active_release = get_compatible_module_release(key, project_version=project_version)
        rows.append({
            "key": key,
            "label": definition.label,
            "description": definition.description,
            "active_release": active_release,
            "compatibilities": compatibilities,
            "env_states": env_states,
        })
    return rows


def is_theme_compatible(theme: ThemeProfile, project_version: ProjectVersion | None = None, environment: str | None = None) -> bool:
    if not theme.is_active:
        return False
    environment = environment or get_runtime_environment()
    if theme.is_test_only and environment == EnvironmentModuleState.Environment.PROD:
        return False
    if project_version is None:
        return True
    if theme.project_version_from and theme.project_version_from.version_order > project_version.version_order:
        return False
    if theme.project_version_to and theme.project_version_to.version_order < project_version.version_order:
        return False
    return True


def get_available_themes(*, project_version: ProjectVersion | None = None, environment: str | None = None):
    project_version = project_version or get_current_project_version()
    environment = environment or get_runtime_environment()
    module_state_map = get_module_state_map(environment=environment)
    themes = [theme for theme in ThemeProfile.objects.order_by("-is_default", "name") if is_theme_compatible(theme, project_version, environment)]
    if not module_state_map.get("midnight_theme", MODULE_REGISTRY["midnight_theme"].default_enabled):
        return [theme for theme in themes if theme.key != "midnight"]
    return themes


def get_active_theme(*, project_version: ProjectVersion | None = None, environment: str | None = None) -> ThemeProfile | None:
    project_version = project_version or get_current_project_version()
    environment = environment or get_runtime_environment()
    config = get_site_configuration()
    available_themes = get_available_themes(project_version=project_version, environment=environment)
    available_theme_keys = {theme.key for theme in available_themes}
    if config.active_theme and is_theme_compatible(config.active_theme, project_version, environment):
        if config.active_theme.key in available_theme_keys:
            return config.active_theme
    theme = ThemeProfile.objects.filter(is_default=True).first()
    if theme and is_theme_compatible(theme, project_version, environment):
        if theme.key in available_theme_keys:
            return theme
    return next(iter(available_themes), None)


def _template_exists(name: str) -> bool:
    try:
        get_template(name)
        return True
    except TemplateDoesNotExist:
        return False


def resolve_theme_template(relative_path: str, fallback: str) -> str:
    theme = get_active_theme()
    if theme and theme.template_base_path:
        candidate = f"{theme.template_base_path}/{relative_path}"
        if _template_exists(candidate):
            return candidate
    return fallback


def get_theme_template_map() -> dict[str, str]:
    return {
        "theme_head_template": resolve_theme_template("components/head.html", "components/head.html"),
        "theme_navbar_public_template": resolve_theme_template("components/navbar_public.html", "components/navbar_public.html"),
        "theme_navbar_testing_template": resolve_theme_template("components/navbar_testing.html", "components/navbar_testing.html"),
        "theme_footer_template": resolve_theme_template("components/footer.html", "components/footer.html"),
    }


def get_control_center_summary(*, config: SiteConfiguration | None = None, state_map: dict[str, bool] | None = None) -> dict[str, object]:
    config = config or get_site_configuration()
    state_map = state_map or get_module_state_map()
    enabled_count = sum(1 for enabled in state_map.values() if enabled)
    total_count = len(MODULE_REGISTRY)
    current_project_version = get_current_project_version()
    active_theme = get_active_theme(project_version=current_project_version)
    return {
        "public_base_url": (config.public_base_url or "—"),
        "maintenance_label": "Enabled" if config.maintenance_mode_enabled else "Disabled",
        "registration_label": "Enabled" if config.registration_enabled else "Disabled",
        "enabled_count": enabled_count,
        "total_count": total_count,
        "tg_notifier_enabled": bool(state_map.get("tg_notifier", False)),
        "steam_catalog_enabled": bool(state_map.get("steam_catalog", False)),
        "subscriptions_enabled": bool(state_map.get("subscriptions", False)),
        "maintenance_mode_enabled": bool(config.maintenance_mode_enabled),
        "registration_enabled": bool(config.registration_enabled),
        "support_email": config.support_email or "—",
        "runtime_environment": get_runtime_environment(),
        "current_project_version": current_project_version,
        "active_theme": active_theme,
    }


@transaction.atomic
def update_module_states(proposed: dict[str, bool], *, environment: str | None = None) -> dict[str, ModuleState]:
    environment = environment or get_runtime_environment()
    states = ensure_module_states()
    current_state_map = {key: states[key].is_enabled for key in MODULE_REGISTRY}
    merged = build_effective_module_state_map(current_state_map, proposed)
    for key, enabled in merged.items():
        state = states[key]
        if state.is_enabled != enabled:
            state.is_enabled = enabled
            state.save(update_fields=["is_enabled", "updated_at"])
        env_state, _created = EnvironmentModuleState.objects.get_or_create(environment=environment, module_key=key, defaults={"is_enabled": enabled})
        if env_state.is_enabled != enabled:
            env_state.is_enabled = enabled
            env_state.save(update_fields=["is_enabled", "updated_at"])
    _sync_compatibility_flags(merged)
    return states


@transaction.atomic
def save_site_configuration(*, config: SiteConfiguration, module_states: dict[str, bool]) -> SiteConfiguration:
    if config.current_project_version_id is None:
        config.current_project_version = get_current_project_version()
    if config.active_theme_id is None:
        config.active_theme = get_active_theme(project_version=config.current_project_version)
    config.save()
    effective_states = build_effective_module_state_map(get_module_state_map(), module_states)
    update_module_states(effective_states)
    tg_config, _created = TelegramNotifierConfig.objects.get_or_create(singleton_key=1, defaults=TelegramNotifierConfig.bootstrap_defaults())
    desired_public_base_url = config.public_base_url
    desired_enabled = effective_states.get("tg_notifier", False)
    changed_fields: list[str] = []
    if tg_config.public_base_url != desired_public_base_url:
        tg_config.public_base_url = desired_public_base_url
        changed_fields.append("public_base_url")
    if tg_config.is_enabled != desired_enabled:
        tg_config.is_enabled = desired_enabled
        changed_fields.append("is_enabled")
    if changed_fields:
        changed_fields.append("updated_at")
        tg_config.save(update_fields=changed_fields)
    return config


@transaction.atomic
def set_active_theme(theme: ThemeProfile) -> SiteConfiguration:
    config = get_site_configuration()
    if not is_theme_compatible(theme, config.current_project_version, get_runtime_environment()):
        raise ValidationError("Тема несовместима с текущей версией проекта или окружением.")
    config.active_theme = theme
    config.save(update_fields=["active_theme", "updated_at"])
    return config


@transaction.atomic
def set_current_project_version(project_version: ProjectVersion) -> SiteConfiguration:
    config = get_site_configuration()
    config.current_project_version = project_version
    active_theme = get_active_theme(project_version=project_version)
    if active_theme:
        config.active_theme = active_theme
    config.save(update_fields=["current_project_version", "active_theme", "updated_at"])
    return config


def _sync_compatibility_flags(module_state_map: dict[str, bool]) -> None:
    tg_enabled = bool(module_state_map.get("tg_notifier", False))
    tg_config, _created = TelegramNotifierConfig.objects.get_or_create(singleton_key=1, defaults=TelegramNotifierConfig.bootstrap_defaults())
    if tg_config.is_enabled != tg_enabled:
        tg_config.is_enabled = tg_enabled
        tg_config.save(update_fields=["is_enabled", "updated_at"])


def is_module_enabled(key: str, environment: str | None = None) -> bool:
    definition = MODULE_REGISTRY.get(key)
    if definition is None:
        return False
    environment = environment or get_runtime_environment()
    env_state = EnvironmentModuleState.objects.filter(environment=environment, module_key=key).select_related("module_release").first()
    if env_state is not None:
        if not env_state.is_enabled:
            return False
        if env_state.module_release and env_state.module_release.channel == ModuleRelease.Channel.TEST and environment == EnvironmentModuleState.Environment.PROD:
            return False
        return True
    return get_module_state_map(environment=environment).get(key, definition.default_enabled)


def get_module_disabled_note(module_key: str, *, environment: str | None = None) -> str:
    module_key = (module_key or "").strip()
    if not module_key:
        return ""
    environment = environment or get_runtime_environment()
    try:
        note = (
            EnvironmentModuleState.objects
            .filter(environment=environment, module_key=module_key)
            .values_list("notes", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        return ""
    return str(note or "").strip()


def resolve_module_for_namespace(namespace: str | None) -> ModuleDefinition | None:
    if not namespace:
        return None
    module_key = NAMESPACE_TO_MODULE_KEY.get(namespace)
    if not module_key:
        return None
    return MODULE_REGISTRY.get(module_key)


def get_public_base_url() -> str:
    config = get_site_configuration()
    return (config.public_base_url or getattr(settings, "SITE_BASE_URL", "") or "").strip().rstrip("/")


def get_site_base_url() -> str:
    return get_public_base_url()


def get_lab_base_url() -> str:
    return (getattr(settings, "CASEUP_LAB_BASE_URL", "") or "").strip().rstrip("/")


def build_absolute_url(path_or_url: str, *, base_url: str = "") -> str:
    raw = (path_or_url or "").strip()
    if not raw:
        return (base_url or "").strip().rstrip("/")
    if "://" in raw:
        return raw
    normalized_base = (base_url or "").strip().rstrip("/")
    if not normalized_base:
        return raw
    normalized_path = raw if raw.startswith("/") else f"/{raw}"
    return f"{normalized_base}{normalized_path}"


def build_site_url(path_or_url: str) -> str:
    return build_absolute_url(path_or_url, base_url=get_site_base_url())


def build_lab_url(path_or_url: str) -> str:
    return build_absolute_url(path_or_url, base_url=get_lab_base_url())


def _normalized_host(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    return host.lstrip(".")


def request_is_lab_host(request) -> bool:
    lab_base_url = get_lab_base_url()
    if not lab_base_url:
        return False
    return _normalized_host(request.get_host()) == _normalized_host(lab_base_url)


def request_is_site_host(request) -> bool:
    site_base_url = get_site_base_url()
    if not site_base_url:
        return False
    return _normalized_host(request.get_host()) == _normalized_host(site_base_url)


def get_site_name() -> str:
    return get_site_configuration().site_name or "CaseUP"


def get_currency_symbol() -> str:
    return get_site_configuration().site_currency_symbol or "₽"


def is_registration_enabled() -> bool:
    return bool(get_site_configuration().registration_enabled)


def quantize_money(value) -> Decimal:
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        raw = value
    else:
        raw = Decimal(str(value))
    return raw.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def get_site_currency_code() -> str:
    return get_site_configuration().site_currency_code or "RUB"


def get_site_currency_symbol() -> str:
    return get_site_configuration().site_currency_symbol or "₽"


def get_site_currency_rate() -> Decimal:
    config = get_site_configuration()
    return Decimal(config.site_currency_rate or "1.000000")


def convert_rub_to_site_currency(value) -> Decimal:
    rub_value = quantize_money(value)
    rate = get_site_currency_rate()
    if rate <= 0:
        rate = Decimal("1.000000")
    return (rub_value / rate).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def format_site_money(value, *, signed: bool = False) -> str:
    amount = convert_rub_to_site_currency(value)
    symbol = get_site_currency_symbol()
    if signed and amount > 0:
        return f"+{amount:.2f} {symbol}"
    return f"{amount:.2f} {symbol}"


def build_page_title(*parts: object) -> str:
    site_name = get_site_name()
    chunks = [str(part).strip() for part in parts if str(part).strip()]
    chunks.append(site_name)
    return " | ".join(chunks)
