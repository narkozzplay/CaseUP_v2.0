from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from .module_registry import MODULE_REGISTRY
from .services import (
    ExternalServiceHealth,
    get_external_service_health,
    get_external_service_integration,
    get_module_state_map,
    get_site_configuration,
)

INTEGRATION_HEALTH_CACHE_KEY = "system:integration_health:v1"
INTEGRATION_HEALTH_TTL_SECONDS = 600

CONFIG_FIELD_LABELS = {
    "site_name": "Название сайта",
    "site_currency_code": "Валюта",
    "site_currency_rate": "Курс валюты",
    "support_email": "Email поддержки",
    "public_base_url": "Public base URL",
    "maintenance_mode_enabled": "Режим обслуживания",
    "maintenance_message": "Текст обслуживания",
    "registration_enabled": "Регистрация",
    "current_project_version": "Версия проекта",
    "active_theme": "Тема",
}
INTEGRATION_FIELD_LABELS = {
    "steam_api_key": "Steam API key",
    "faceit_api_key": "FaceIT API key",
    "telegram_bot_token": "Telegram bot token",
    "steam_health_mode": "Steam health mode",
    "faceit_health_mode": "FaceIT health mode",
    "telegram_health_mode": "Telegram health mode",
    "degraded_stub_message": "Заглушка degraded",
    "down_stub_message": "Заглушка down",
}
SECRET_FIELDS = {"steam_api_key", "faceit_api_key", "telegram_bot_token"}


def get_cached_integration_health(*, refresh: bool = False) -> dict[str, object]:
    """Live checks hit external APIs, so they run on demand and are cached."""
    payload = None if refresh else cache.get(INTEGRATION_HEALTH_CACHE_KEY)
    if payload is None and not refresh:
        return {"checked_at": None, "items": _unchecked_health()}
    if payload is None:
        items = get_external_service_health()
        payload = {"checked_at": timezone.now().isoformat(), "items": [asdict(item) for item in items]}
        cache.set(INTEGRATION_HEALTH_CACHE_KEY, payload, INTEGRATION_HEALTH_TTL_SECONDS)
    checked_at = payload.get("checked_at")
    return {
        "checked_at": datetime.fromisoformat(checked_at) if checked_at else None,
        "items": [ExternalServiceHealth(**item) for item in payload["items"]],
    }


def _unchecked_health() -> list[ExternalServiceHealth]:
    integration = get_external_service_integration()
    rows = (
        ("steam", "Steam API", integration.steam_api_key, integration.steam_health_mode),
        ("faceit", "FaceIT API", integration.faceit_api_key, integration.faceit_health_mode),
        ("telegram", "Telegram Bot API", integration.telegram_bot_token, integration.telegram_health_mode),
    )
    return [
        ExternalServiceHealth(
            key=key,
            label=label,
            status="unchecked" if secret else "missing_key",
            mode=mode,
            has_key=bool(secret),
            message="Нажмите «Проверить сейчас», чтобы опросить API." if secret else "API ключ не задан.",
        )
        for key, label, secret, mode in rows
    ]


def snapshot_settings() -> dict[str, object]:
    config = get_site_configuration()
    integration = get_external_service_integration()
    data: dict[str, object] = {}
    for field in CONFIG_FIELD_LABELS:
        value = getattr(config, field)
        data[f"config.{field}"] = str(value) if value is not None else ""
    for field in INTEGRATION_FIELD_LABELS:
        data[f"integration.{field}"] = getattr(integration, field) or ""
    for key, enabled in get_module_state_map().items():
        data[f"module.{key}"] = bool(enabled)
    return data


def diff_settings(before: dict[str, object], after: dict[str, object]) -> list[dict[str, object]]:
    changes = []
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value == new_value:
            continue
        scope, field = key.split(".", 1)
        if scope == "config":
            label = CONFIG_FIELD_LABELS.get(field, field)
        elif scope == "integration":
            label = INTEGRATION_FIELD_LABELS.get(field, field)
        else:
            definition = MODULE_REGISTRY.get(field)
            label = f"Модуль {definition.label if definition else field}"
        if scope == "integration" and field in SECRET_FIELDS:
            changes.append({"field": key, "label": label, "from": "••••" if old_value else "—", "to": "••••" if new_value else "—"})
        else:
            changes.append({"field": key, "label": label, "from": _short(old_value), "to": _short(new_value)})
    return changes


def _short(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value if value is not None else "")
    return (text[:60] + "…") if len(text) > 60 else (text or "—")


def export_settings_payload() -> dict[str, object]:
    config = get_site_configuration()
    integration = get_external_service_integration()
    rate = config.site_currency_rate
    return {
        "exported_at": timezone.now().isoformat(),
        "site": {
            "site_name": config.site_name,
            "site_currency_code": config.site_currency_code,
            "site_currency_rate": str(rate if isinstance(rate, Decimal) else Decimal(str(rate))),
            "support_email": config.support_email,
            "public_base_url": config.public_base_url,
            "maintenance_mode_enabled": config.maintenance_mode_enabled,
            "maintenance_message": config.maintenance_message,
            "registration_enabled": config.registration_enabled,
            "current_project_version": getattr(config.current_project_version, "version", None),
            "active_theme": getattr(config.active_theme, "key", None),
        },
        "integrations": {
            "steam_api_key_set": bool(integration.steam_api_key),
            "faceit_api_key_set": bool(integration.faceit_api_key),
            "telegram_bot_token_set": bool(integration.telegram_bot_token),
            "steam_health_mode": integration.steam_health_mode,
            "faceit_health_mode": integration.faceit_health_mode,
            "telegram_health_mode": integration.telegram_health_mode,
        },
        "modules": get_module_state_map(),
    }
