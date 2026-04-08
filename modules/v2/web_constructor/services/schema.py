from __future__ import annotations

import copy
import uuid
from typing import Any
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


class TemplateSchemaError(ValidationError):
    pass


PAGE_STYLE_MANIFEST = {
    "theme": {"label": "Theme", "choices": ["light", "dark", "contrast"]},
    "accent": {"label": "Accent", "choices": ["blue", "emerald", "violet", "amber", "rose", "slate"]},
    "width": {"label": "Content width", "choices": ["narrow", "standard", "wide"]},
    "spacing": {"label": "Vertical spacing", "choices": ["compact", "normal", "relaxed"]},
    "font_scale": {"label": "Font scale", "choices": ["sm", "md", "lg"]},
    "surface": {"label": "Surface", "choices": ["plain", "soft", "gradient"]},
    "radius": {"label": "Card radius", "choices": ["sm", "md", "lg"]},
    "background_asset_id": {"label": "Background asset", "type": "asset"},
}

BLOCK_LIBRARY: dict[str, dict[str, Any]] = {
    "hero": {
        "label": "Hero",
        "template": "web_constructor/render/blocks/hero.html",
        "default_data": {
            "eyebrow": "New module",
            "title": "Page hero title",
            "text": "Короткое описание главного предложения или сценария использования.",
            "primary_label": "Primary action",
            "primary_url": "/",
            "secondary_label": "Learn more",
            "secondary_url": "/",
            "image_asset_id": None,
            "image_url": "",
        },
        "default_style": {"variant": "accent", "align": "left", "padding": "lg"},
        "data_fields": [
            {"name": "eyebrow", "kind": "text", "max_length": 80},
            {"name": "title", "kind": "text", "max_length": 180},
            {"name": "text", "kind": "textarea", "max_length": 900},
            {"name": "primary_label", "kind": "text", "max_length": 60},
            {"name": "primary_url", "kind": "url", "max_length": 255},
            {"name": "secondary_label", "kind": "text", "max_length": 60},
            {"name": "secondary_url", "kind": "url", "max_length": 255},
            {"name": "image_asset_id", "kind": "asset"},
            {"name": "image_url", "kind": "url", "max_length": 255},
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "accent", "dark"]},
            {"name": "align", "kind": "choice", "choices": ["left", "center"]},
            {"name": "padding", "kind": "choice", "choices": ["md", "lg", "xl"]},
        ],
    },
    "banner": {
        "label": "Banner",
        "template": "web_constructor/render/blocks/banner.html",
        "default_data": {
            "label": "Announcement",
            "title": "Important banner",
            "text": "Короткое уведомление, акция или системный баннер.",
            "button_label": "Open",
            "button_url": "/",
            "background_asset_id": None,
            "background_image_url": "",
        },
        "default_style": {"variant": "soft", "align": "left"},
        "data_fields": [
            {"name": "label", "kind": "text", "max_length": 80},
            {"name": "title", "kind": "text", "max_length": 180},
            {"name": "text", "kind": "textarea", "max_length": 700},
            {"name": "button_label", "kind": "text", "max_length": 60},
            {"name": "button_url", "kind": "url", "max_length": 255},
            {"name": "background_asset_id", "kind": "asset"},
            {"name": "background_image_url", "kind": "url", "max_length": 255},
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft", "accent", "dark"]},
            {"name": "align", "kind": "choice", "choices": ["left", "center"]},
        ],
    },
    "stats": {
        "label": "Stats",
        "template": "web_constructor/render/blocks/stats.html",
        "default_data": {
            "title": "Project stats",
            "items": [
                {"label": "Templates", "value": "12", "description": "saved layouts"},
                {"label": "Preview", "value": "Live", "description": "safe renderer"},
            ],
        },
        "default_style": {"variant": "plain", "columns": "3"},
        "data_fields": [
            {"name": "title", "kind": "text", "max_length": 180},
            {
                "name": "items",
                "kind": "repeater",
                "max_items": 8,
                "fields": [
                    {"name": "label", "kind": "text", "max_length": 60},
                    {"name": "value", "kind": "text", "max_length": 60},
                    {"name": "description", "kind": "text", "max_length": 160},
                ],
            },
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft", "accent"]},
            {"name": "columns", "kind": "choice", "choices": ["2", "3", "4"]},
        ],
    },
    "cards": {
        "label": "Cards",
        "template": "web_constructor/render/blocks/cards.html",
        "default_data": {
            "title": "Cards section",
            "items": [
                {"title": "Card 1", "text": "Описание карточки.", "badge": "Core", "button_label": "Open", "button_url": "/", "image_asset_id": None, "image_url": ""},
                {"title": "Card 2", "text": "Вторая карточка.", "badge": "Optional", "button_label": "Open", "button_url": "/", "image_asset_id": None, "image_url": ""},
            ],
        },
        "default_style": {"variant": "plain", "columns": "3"},
        "data_fields": [
            {"name": "title", "kind": "text", "max_length": 180},
            {
                "name": "items",
                "kind": "repeater",
                "max_items": 8,
                "fields": [
                    {"name": "title", "kind": "text", "max_length": 120},
                    {"name": "text", "kind": "textarea", "max_length": 400},
                    {"name": "badge", "kind": "text", "max_length": 40},
                    {"name": "button_label", "kind": "text", "max_length": 40},
                    {"name": "button_url", "kind": "url", "max_length": 255},
                    {"name": "image_asset_id", "kind": "asset"},
                    {"name": "image_url", "kind": "url", "max_length": 255},
                ],
            },
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft", "accent"]},
            {"name": "columns", "kind": "choice", "choices": ["2", "3", "4"]},
        ],
    },
    "faq": {
        "label": "FAQ",
        "template": "web_constructor/render/blocks/faq.html",
        "default_data": {
            "title": "FAQ",
            "items": [
                {"question": "Как это работает?", "answer": "Через безопасную JSON-схему и серверный renderer."},
                {"question": "Можно ли вставить JS?", "answer": "Нет, произвольный JS запрещён."},
            ],
        },
        "default_style": {"variant": "plain"},
        "data_fields": [
            {"name": "title", "kind": "text", "max_length": 180},
            {
                "name": "items",
                "kind": "repeater",
                "max_items": 12,
                "fields": [
                    {"name": "question", "kind": "text", "max_length": 180},
                    {"name": "answer", "kind": "textarea", "max_length": 600},
                ],
            },
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft"]},
        ],
    },
    "pricing": {
        "label": "Pricing",
        "template": "web_constructor/render/blocks/pricing.html",
        "default_data": {
            "title": "Pricing plans",
            "subtitle": "Секция для тарифов или пакетов услуг.",
            "items": [
                {"name": "Starter", "price": "0 ₽", "period": "навсегда", "features_text": "1 шаблон\nБазовый preview", "button_label": "Choose", "button_url": "/", "badge": "Free"},
                {"name": "Pro", "price": "990 ₽", "period": "в месяц", "features_text": "Неограниченно\nРасширенный доступ", "button_label": "Choose", "button_url": "/", "badge": "Popular"},
            ],
        },
        "default_style": {"variant": "plain", "columns": "3"},
        "data_fields": [
            {"name": "title", "kind": "text", "max_length": 180},
            {"name": "subtitle", "kind": "textarea", "max_length": 500},
            {
                "name": "items",
                "kind": "repeater",
                "max_items": 6,
                "fields": [
                    {"name": "name", "kind": "text", "max_length": 80},
                    {"name": "price", "kind": "text", "max_length": 80},
                    {"name": "period", "kind": "text", "max_length": 80},
                    {"name": "features_text", "kind": "textarea", "max_length": 600},
                    {"name": "button_label", "kind": "text", "max_length": 40},
                    {"name": "button_url", "kind": "url", "max_length": 255},
                    {"name": "badge", "kind": "text", "max_length": 40},
                ],
            },
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft", "accent"]},
            {"name": "columns", "kind": "choice", "choices": ["2", "3"]},
        ],
    },
    "profile_card": {
        "label": "Profile card",
        "template": "web_constructor/render/blocks/profile_card.html",
        "default_data": {
            "title": "Author / profile card",
            "name": "CaseUP user",
            "subtitle": "Builder",
            "bio": "Карточка автора, команды или профиля.",
            "avatar_asset_id": None,
            "avatar_url": "",
            "profile_url": "/profile/",
        },
        "default_style": {"variant": "soft", "align": "left"},
        "data_fields": [
            {"name": "title", "kind": "text", "max_length": 180},
            {"name": "name", "kind": "text", "max_length": 120},
            {"name": "subtitle", "kind": "text", "max_length": 120},
            {"name": "bio", "kind": "textarea", "max_length": 700},
            {"name": "avatar_asset_id", "kind": "asset"},
            {"name": "avatar_url", "kind": "url", "max_length": 255},
            {"name": "profile_url", "kind": "url", "max_length": 255},
        ],
        "style_fields": [
            {"name": "variant", "kind": "choice", "choices": ["plain", "soft", "accent"]},
            {"name": "align", "kind": "choice", "choices": ["left", "center"]},
        ],
    },
}


DEFAULT_PAGE_STYLE = {
    "theme": "light",
    "accent": "blue",
    "width": "standard",
    "spacing": "normal",
    "font_scale": "md",
    "surface": "plain",
    "radius": "md",
    "background_asset_id": None,
}


def build_default_schema(title: str = "") -> dict[str, Any]:
    hero = build_default_block("hero")
    if title:
        hero["data"]["title"] = title
    return {
        "page": {"style": copy.deepcopy(DEFAULT_PAGE_STYLE)},
        "blocks": [hero],
    }


def build_default_block(block_type: str) -> dict[str, Any]:
    preset = BLOCK_LIBRARY[block_type]
    return {
        "id": f"blk_{uuid.uuid4().hex[:10]}",
        "type": block_type,
        "visible": True,
        "data": copy.deepcopy(preset["default_data"]),
        "style": copy.deepcopy(preset["default_style"]),
    }


def get_editor_manifest() -> dict[str, Any]:
    return {
        "page_style": PAGE_STYLE_MANIFEST,
        "blocks": {
            key: {
                "label": spec["label"],
                "default_data": copy.deepcopy(spec["default_data"]),
                "default_style": copy.deepcopy(spec["default_style"]),
                "data_fields": copy.deepcopy(spec["data_fields"]),
                "style_fields": copy.deepcopy(spec["style_fields"]),
            }
            for key, spec in BLOCK_LIBRARY.items()
        },
    }


def _normalize_text(value: Any, *, max_length: int = 255) -> str:
    value = "" if value is None else str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > max_length:
        value = value[:max_length].rstrip()
    return value


def _normalize_url(value: Any) -> str:
    raw = _normalize_text(value, max_length=255)
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TemplateSchemaError("В схеме обнаружен недопустимый URL.")
    return raw


def _is_external_url(url: str, site_base_url: str = "") -> bool:
    if not url:
        return False
    if url.startswith("/"):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not site_base_url:
        return True
    site = urlparse(site_base_url)
    return (parsed.netloc or "").lower() != (site.netloc or "").lower()


def _normalize_field(spec: dict[str, Any], value: Any, *, asset_ids: set[int], site_base_url: str) -> tuple[Any, list[str]]:
    kind = spec.get("kind")
    external_links: list[str] = []
    if kind == "text":
        return _normalize_text(value, max_length=spec.get("max_length", 255)), external_links
    if kind == "textarea":
        return _normalize_text(value, max_length=spec.get("max_length", 4000)), external_links
    if kind == "choice":
        allowed = list(spec.get("choices", []))
        normalized = _normalize_text(value, max_length=64)
        if normalized not in allowed:
            normalized = allowed[0] if allowed else ""
        return normalized, external_links
    if kind == "url":
        normalized = _normalize_url(value)
        if _is_external_url(normalized, site_base_url=site_base_url):
            external_links.append(normalized)
        return normalized, external_links
    if kind == "asset":
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None, external_links
        return (normalized if normalized in asset_ids else None), external_links
    if kind == "bool":
        return bool(value), external_links
    if kind == "repeater":
        items = value if isinstance(value, list) else []
        max_items = int(spec.get("max_items", 8))
        normalized_items: list[dict[str, Any]] = []
        nested_specs = spec.get("fields", [])
        for item in items[:max_items]:
            row = item if isinstance(item, dict) else {}
            normalized_row: dict[str, Any] = {}
            for nested in nested_specs:
                normalized_value, nested_links = _normalize_field(
                    nested,
                    row.get(nested["name"]),
                    asset_ids=asset_ids,
                    site_base_url=site_base_url,
                )
                normalized_row[nested["name"]] = normalized_value
                external_links.extend(nested_links)
            normalized_items.append(normalized_row)
        return normalized_items, external_links
    return _normalize_text(value, max_length=255), external_links


def normalize_page_style(style: Any, *, asset_ids: set[int]) -> dict[str, Any]:
    style = style if isinstance(style, dict) else {}
    normalized: dict[str, Any] = {}
    for key, spec in PAGE_STYLE_MANIFEST.items():
        if spec.get("type") == "asset":
            try:
                asset_id = int(style.get(key))
            except (TypeError, ValueError):
                asset_id = None
            normalized[key] = asset_id if asset_id in asset_ids else None
            continue
        choices = spec.get("choices", [])
        value = _normalize_text(style.get(key), max_length=32)
        normalized[key] = value if value in choices else DEFAULT_PAGE_STYLE[key]
    return normalized


def normalize_schema(schema: Any, *, asset_ids: set[int] | None = None, site_base_url: str = "") -> tuple[dict[str, Any], list[str]]:
    if not isinstance(schema, dict):
        raise TemplateSchemaError("Schema JSON должен быть объектом.")
    asset_ids = asset_ids or set()
    page = schema.get("page") if isinstance(schema.get("page"), dict) else {}
    blocks = schema.get("blocks") if isinstance(schema.get("blocks"), list) else []
    normalized_blocks: list[dict[str, Any]] = []
    external_links: list[str] = []

    for raw_block in blocks[:20]:
        block = raw_block if isinstance(raw_block, dict) else {}
        block_type = _normalize_text(block.get("type"), max_length=40)
        preset = BLOCK_LIBRARY.get(block_type)
        if preset is None:
            continue
        normalized_data = copy.deepcopy(preset["default_data"])
        normalized_style = copy.deepcopy(preset["default_style"])
        block_links: list[str] = []
        block_data = block.get("data") if isinstance(block.get("data"), dict) else {}
        block_style = block.get("style") if isinstance(block.get("style"), dict) else {}
        for field in preset.get("data_fields", []):
            value, field_links = _normalize_field(field, block_data.get(field["name"]), asset_ids=asset_ids, site_base_url=site_base_url)
            normalized_data[field["name"]] = value
            block_links.extend(field_links)
        for field in preset.get("style_fields", []):
            value, field_links = _normalize_field(field, block_style.get(field["name"]), asset_ids=asset_ids, site_base_url=site_base_url)
            normalized_style[field["name"]] = value
            block_links.extend(field_links)
        normalized_blocks.append(
            {
                "id": _normalize_text(block.get("id"), max_length=64) or f"blk_{uuid.uuid4().hex[:10]}",
                "type": block_type,
                "label": preset["label"],
                "template_name": preset["template"],
                "visible": bool(block.get("visible", True)),
                "data": normalized_data,
                "style": normalized_style,
            }
        )
        external_links.extend(block_links)

    if not normalized_blocks:
        normalized_blocks.append(build_default_block("hero"))

    normalized_schema = {
        "page": {"style": normalize_page_style(page.get("style"), asset_ids=asset_ids)},
        "blocks": normalized_blocks,
    }
    deduped_links = list(dict.fromkeys(link for link in external_links if link))
    return normalized_schema, deduped_links
