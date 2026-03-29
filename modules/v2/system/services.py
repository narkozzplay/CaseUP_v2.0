from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from modules.v2.tg_notifier.models import TelegramNotifierConfig

from .module_registry import MODULE_REGISTRY, NAMESPACE_TO_MODULE_KEY, ModuleDefinition
from .models import ModuleState, SiteConfiguration


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


def get_site_configuration() -> SiteConfiguration:
    config = SiteConfiguration.objects.filter(singleton_key=1).first()
    if config is not None:
        return config
    config, _created = SiteConfiguration.objects.get_or_create(
        singleton_key=1,
        defaults=SiteConfiguration.bootstrap_defaults(),
    )
    return config


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


def get_module_state_map() -> dict[str, bool]:
    state_map = {
        key: definition.default_enabled
        for key, definition in MODULE_REGISTRY.items()
    }
    persisted = ModuleState.objects.filter(key__in=MODULE_REGISTRY).values_list("key", "is_enabled")
    for key, is_enabled in persisted:
        state_map[key] = is_enabled
    if "tg_notifier" in state_map and not ModuleState.objects.filter(key="tg_notifier").exists():
        state_map["tg_notifier"] = bool(getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False))
    return state_map


def _get_dependents_map() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}
    for key, definition in MODULE_REGISTRY.items():
        for dependency_key in definition.dependencies:
            mapping.setdefault(dependency_key, []).append(key)
    return {
        key: tuple(values)
        for key, values in mapping.items()
    }


def _get_addon_maps() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    source_to_targets: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}
    target_to_sources: dict[str, list[str]] = {key: [] for key in MODULE_REGISTRY}

    for key, definition in MODULE_REGISTRY.items():
        for target_key in definition.addon_targets:
            if target_key not in MODULE_REGISTRY:
                continue
            source_to_targets.setdefault(key, []).append(target_key)
            target_to_sources.setdefault(target_key, []).append(key)

    return (
        {key: tuple(values) for key, values in source_to_targets.items()},
        {key: tuple(values) for key, values in target_to_sources.items()},
    )


def get_module_state_views(state_map: dict[str, bool] | None = None) -> list[ModuleStateView]:
    state_map = state_map or get_module_state_map()
    dependents_map = _get_dependents_map()
    addon_targets_map, addon_sources_map = _get_addon_maps()

    rows: list[ModuleStateView] = []
    for key, definition in MODULE_REGISTRY.items():
        is_enabled = state_map.get(key, definition.default_enabled)
        missing_dependencies = tuple(
            dependency_key
            for dependency_key in definition.dependencies
            if not state_map.get(dependency_key, False)
        )
        dependent_keys = dependents_map.get(key, ())
        cascade_disable_labels = tuple(
            MODULE_REGISTRY[dependent_key].label
            for dependent_key in dependent_keys
            if state_map.get(dependent_key, MODULE_REGISTRY[dependent_key].default_enabled)
        )

        if is_enabled:
            if missing_dependencies:
                status_label = "Conflict"
            else:
                status_label = "Enabled"
        else:
            if missing_dependencies:
                status_label = "Blocked"
            else:
                status_label = "Disabled"

        if missing_dependencies:
            missing_dependency_labels = tuple(
                MODULE_REGISTRY[item].label for item in missing_dependencies
            )
            blocked_reason = (
                "Нельзя включить, пока выключены зависимости: "
                + ", ".join(missing_dependency_labels)
                + "."
            )
        else:
            missing_dependency_labels = ()
            blocked_reason = ""

        rows.append(
            ModuleStateView(
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
                addon_target_labels=tuple(
                    MODULE_REGISTRY[item].label for item in addon_targets_map.get(key, ())
                ),
                addon_source_keys=addon_sources_map.get(key, ()),
                addon_source_labels=tuple(
                    MODULE_REGISTRY[item].label for item in addon_sources_map.get(key, ())
                ),
                can_enable=not bool(missing_dependencies),
                blocked_reason=blocked_reason,
                status_label=status_label,
            )
        )
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
            raise ValidationError(
                f"Нельзя включить модуль '{definition.label}', пока выключены зависимости: {dep_labels}."
            )


def build_effective_module_state_map(
    current_state_map: dict[str, bool],
    proposed: dict[str, bool],
) -> dict[str, bool]:
    normalized_proposed = {key: bool(value) for key, value in proposed.items()}
    merged = {
        key: current_state_map.get(key, definition.default_enabled)
        for key, definition in MODULE_REGISTRY.items()
    }
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

            # Если модуль раньше был выключен, а сейчас его пытаются включить
            # при выключенной зависимости — это ошибка.
            if not was_enabled_before and explicitly_enabled_now:
                dep_labels = ", ".join(MODULE_REGISTRY[item].label for item in missing)
                raise ValidationError(
                    f"Нельзя включить модуль '{definition.label}', пока выключены зависимости: {dep_labels}."
                )

            # Если модуль раньше уже был включён, но его зависимость выключили
            # в этом же запросе — выключаем каскадно.
            merged[key] = False
            changed = True

    validate_module_state_map(merged)
    return merged


def preview_module_update(
    current_state_map: dict[str, bool],
    proposed: dict[str, bool],
) -> ModuleUpdatePreview:
    merged = build_effective_module_state_map(current_state_map, proposed)

    direct_disabled_keys = tuple(
        key
        for key, value in proposed.items()
        if not bool(value) and current_state_map.get(key, MODULE_REGISTRY[key].default_enabled)
    )
    cascaded_disabled_keys = tuple(
        key
        for key in MODULE_REGISTRY
        if current_state_map.get(key, MODULE_REGISTRY[key].default_enabled)
        and not merged.get(key, MODULE_REGISTRY[key].default_enabled)
        and key not in direct_disabled_keys
    )
    changed_keys = tuple(
        key
        for key in MODULE_REGISTRY
        if current_state_map.get(key, MODULE_REGISTRY[key].default_enabled)
        != merged.get(key, MODULE_REGISTRY[key].default_enabled)
    )

    return ModuleUpdatePreview(
        merged_state_map=merged,
        direct_disabled_keys=direct_disabled_keys,
        direct_disabled_labels=tuple(MODULE_REGISTRY[key].label for key in direct_disabled_keys),
        cascaded_disabled_keys=cascaded_disabled_keys,
        cascaded_disabled_labels=tuple(MODULE_REGISTRY[key].label for key in cascaded_disabled_keys),
        changed_keys=changed_keys,
        changed_labels=tuple(MODULE_REGISTRY[key].label for key in changed_keys),
    )


def get_control_center_summary(
    *,
    config: SiteConfiguration | None = None,
    state_map: dict[str, bool] | None = None,
) -> dict[str, object]:
    config = config or get_site_configuration()
    state_map = state_map or get_module_state_map()

    enabled_count = sum(1 for enabled in state_map.values() if enabled)
    total_count = len(MODULE_REGISTRY)

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
    }


@transaction.atomic
def update_module_states(proposed: dict[str, bool]) -> dict[str, ModuleState]:
    states = ensure_module_states()
    current_state_map = {
        key: states[key].is_enabled
        for key in MODULE_REGISTRY
    }
    merged = build_effective_module_state_map(current_state_map, proposed)

    for key, enabled in merged.items():
        state = states[key]
        if state.is_enabled != enabled:
            state.is_enabled = enabled
            state.save(update_fields=["is_enabled", "updated_at"])

    _sync_compatibility_flags(merged)
    return states


@transaction.atomic
def save_site_configuration(*, config: SiteConfiguration, module_states: dict[str, bool]) -> SiteConfiguration:
    config.save()
    effective_states = build_effective_module_state_map(get_module_state_map(), module_states)
    update_module_states(effective_states)

    tg_config, _created = TelegramNotifierConfig.objects.get_or_create(
        singleton_key=1,
        defaults=TelegramNotifierConfig.bootstrap_defaults(),
    )
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


def _sync_compatibility_flags(module_state_map: dict[str, bool]) -> None:
    tg_enabled = bool(module_state_map.get("tg_notifier", False))
    tg_config, _created = TelegramNotifierConfig.objects.get_or_create(
        singleton_key=1,
        defaults=TelegramNotifierConfig.bootstrap_defaults(),
    )
    if tg_config.is_enabled != tg_enabled:
        tg_config.is_enabled = tg_enabled
        tg_config.save(update_fields=["is_enabled", "updated_at"])


def is_module_enabled(key: str) -> bool:
    definition = MODULE_REGISTRY.get(key)
    if definition is None:
        return False
    return get_module_state_map().get(key, definition.default_enabled)


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


def get_site_name() -> str:
    return get_site_configuration().site_name or "CaseUP"


def get_currency_symbol() -> str:
    return get_site_configuration().site_currency_symbol or "₽"


def is_registration_enabled() -> bool:
    return bool(get_site_configuration().registration_enabled)
