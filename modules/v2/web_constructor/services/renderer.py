from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.template.loader import render_to_string

from modules.v2.system.services import get_site_configuration

from ..models import PageTemplate, PageTemplateVersion, TemplateAsset
from .schema import normalize_schema


def _build_asset_map(template: PageTemplate) -> dict[int, dict[str, str]]:
    queryset = TemplateAsset.objects.filter(owner=template.owner)
    if template.pk:
        queryset = queryset.filter(Q(template__isnull=True) | Q(template=template))
    else:
        queryset = queryset.filter(template__isnull=True)
    asset_map: dict[int, dict[str, str]] = {}
    for asset in queryset.order_by("-created_at", "-id"):
        asset_map[asset.id] = {
            "url": asset.file.url if asset.file else "",
            "title": asset.title or asset.alt_text or asset.file.name,
            "alt": asset.alt_text or asset.title or "",
            "type": asset.asset_type,
        }
    return asset_map


def _attach_asset_urls(value: Any, asset_map: dict[int, dict[str, str]]) -> Any:
    if isinstance(value, list):
        return [_attach_asset_urls(item, asset_map) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, raw in value.items():
        result[key] = _attach_asset_urls(raw, asset_map)
        if key.endswith("_asset_id") and raw:
            asset = asset_map.get(raw)
            result[key[:-3] + "url"] = asset["url"] if asset else ""
            result[key[:-3] + "title"] = asset["title"] if asset else ""
            result[key[:-3] + "alt"] = asset["alt"] if asset else ""
    return result


def build_render_payload(*, template: PageTemplate, version: PageTemplateVersion | None = None, schema_override: dict[str, Any] | None = None) -> dict[str, Any]:
    version = version or template.approved_version or template.current_version
    schema_source = schema_override if schema_override is not None else (version.schema_json if version else {})
    site_settings = get_site_configuration()
    asset_map = _build_asset_map(template)
    normalized_schema, external_links = normalize_schema(
        schema_source,
        asset_ids=set(asset_map.keys()),
        site_base_url=site_settings.public_base_url,
    )
    page_style = normalized_schema["page"]["style"]
    blocks = []
    for block in normalized_schema["blocks"]:
        prepared = dict(block)
        prepared["data"] = _attach_asset_urls(block["data"], asset_map)
        prepared["style_classes"] = " ".join(
            [
                f"wc-block--{block['type']}",
                *[f"wc-block--{key}-{value}" for key, value in block["style"].items() if value not in {None, ''}],
            ]
        )
        blocks.append(prepared)
    background_asset = asset_map.get(page_style.get("background_asset_id")) if page_style.get("background_asset_id") else None
    return {
        "site_settings": site_settings,
        "template_obj": template,
        "version": version,
        "page": {
            "style": page_style,
            "classes": " ".join(
                [
                    "wc-page",
                    f"wc-page--theme-{page_style['theme']}",
                    f"wc-page--accent-{page_style['accent']}",
                    f"wc-page--width-{page_style['width']}",
                    f"wc-page--spacing-{page_style['spacing']}",
                    f"wc-page--font-{page_style['font_scale']}",
                    f"wc-page--surface-{page_style['surface']}",
                    f"wc-page--radius-{page_style['radius']}",
                ]
            ),
            "background_url": background_asset["url"] if background_asset else "",
            "background_alt": background_asset["alt"] if background_asset else "",
        },
        "blocks": blocks,
        "external_links": external_links,
    }


def render_preview_fragment(*, template: PageTemplate, version: PageTemplateVersion | None = None, schema_override: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    payload = build_render_payload(template=template, version=version, schema_override=schema_override)
    html = render_to_string("web_constructor/render/page_body.html", payload)
    return html, payload
