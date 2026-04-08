from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from modules.v2.web_constructor.models import EditablePage, SandboxSession, StylePreset


def ensure_style_presets() -> list[StylePreset]:
    specs = [
        {"key": "default-page", "label": "Default page", "scope": StylePreset.Scope.PAGE, "description": "Базовый пресет страницы проекта.", "config_json": {"background": "#0b1020", "surface": "#11182d", "text": "#ffffff", "radius": "18px"}, "is_default": True, "is_active": True},
        {"key": "contrast-page", "label": "Contrast page", "scope": StylePreset.Scope.PAGE, "description": "Контрастный пресет для preview и sandbox.", "config_json": {"background": "#ffffff", "surface": "#f5f7fb", "text": "#111111", "radius": "18px"}, "is_default": False, "is_active": True},
        {"key": "glass-area", "label": "Glass area", "scope": StylePreset.Scope.AREA, "description": "Полупрозрачный area preset.", "config_json": {"backdrop": "blur(14px)", "border": "1px solid rgba(255,255,255,.12)"}, "is_default": True, "is_active": True},
    ]
    presets = []
    for spec in specs:
        preset, _created = StylePreset.objects.update_or_create(key=spec["key"], defaults={k: v for k, v in spec.items() if k != "key"})
        presets.append(preset)
    return presets


def get_or_create_sandbox_session(*, owner, editable_page: EditablePage | None = None) -> SandboxSession:
    session = SandboxSession.objects.filter(owner=owner, status=SandboxSession.Status.DRAFT).order_by("-updated_at").first()
    if session:
        if editable_page and session.editable_page_id != editable_page.id:
            session.editable_page = editable_page
            session.preview_url = editable_page.route_path
            session.save(update_fields=["editable_page", "preview_url", "updated_at"])
        return session
    payload = {
        "pages": {},
        "ui": {"selected_variable": None, "undo": [], "redo": []},
    }
    return SandboxSession.objects.create(
        owner=owner,
        editable_page=editable_page,
        preview_url=editable_page.route_path if editable_page else "",
        draft_payload=payload,
    )


@transaction.atomic
def save_sandbox_draft(*, session: SandboxSession, editable_page: EditablePage, payload: dict) -> SandboxSession:
    draft = dict(session.draft_payload or {})
    pages = dict(draft.get("pages") or {})
    pages[editable_page.route_key] = payload
    draft["pages"] = pages
    session.editable_page = editable_page
    session.preview_url = editable_page.route_path
    session.draft_payload = draft
    session.last_saved_at = timezone.now()
    session.status = SandboxSession.Status.DRAFT
    session.save(update_fields=["editable_page", "preview_url", "draft_payload", "last_saved_at", "status", "updated_at"])
    return session


def reset_sandbox_page(*, session: SandboxSession, editable_page: EditablePage) -> SandboxSession:
    draft = dict(session.draft_payload or {})
    pages = dict(draft.get("pages") or {})
    pages.pop(editable_page.route_key, None)
    draft["pages"] = pages
    session.draft_payload = draft
    session.save(update_fields=["draft_payload", "updated_at"])
    return session


def build_builder_state(*, session: SandboxSession, editable_page: EditablePage) -> dict:
    draft = session.draft_payload or {}
    page_state = (draft.get("pages") or {}).get(editable_page.route_key) or {}
    return {
        "session_id": session.id,
        "page": {
            "id": editable_page.id,
            "route_key": editable_page.route_key,
            "title": editable_page.title,
            "route_path": editable_page.route_path,
            "url_name": editable_page.url_name,
            "layout_template": editable_page.layout_template,
            "template_name": editable_page.template_name,
            "requires_auth": editable_page.requires_auth,
            "is_dynamic": editable_page.is_dynamic,
        },
        "draft": page_state,
        "areas": [
            {
                "id": area.id,
                "area_key": area.area_key,
                "label": area.label,
                "area_type": area.area_type,
                "sort_order": area.sort_order,
                "default_content": area.default_content,
                "default_styles": area.default_styles,
                "meta": area.meta_json,
            }
            for area in editable_page.areas.filter(is_active=True).order_by("sort_order", "id")
        ],
        "style_presets": [
            {
                "id": preset.id,
                "key": preset.key,
                "label": preset.label,
                "scope": preset.scope,
                "config": preset.config_json,
                "is_default": preset.is_default,
            }
            for preset in StylePreset.objects.filter(is_active=True).order_by("scope", "label")
        ],
    }
