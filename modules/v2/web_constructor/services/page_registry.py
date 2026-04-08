from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Iterable

from django.urls import URLPattern, URLResolver, get_resolver
from django.utils.text import slugify

from modules.v2.web_constructor.models import EditableArea, EditablePage


AUTO_AREA_SPECS = [
    {"area_key": "page_header", "label": "Page header", "area_type": EditableArea.AreaType.HEADER, "sort_order": 10},
    {"area_key": "hero", "label": "Hero / lead block", "area_type": EditableArea.AreaType.HERO, "sort_order": 20},
    {"area_key": "content", "label": "Main content", "area_type": EditableArea.AreaType.CONTENT, "sort_order": 30},
    {"area_key": "sidebar", "label": "Sidebar", "area_type": EditableArea.AreaType.SIDEBAR, "sort_order": 40},
    {"area_key": "page_footer", "label": "Page footer", "area_type": EditableArea.AreaType.FOOTER, "sort_order": 50},
]


@dataclass(slots=True)
class DiscoveredRoute:
    route_key: str
    title: str
    route_path: str
    url_name: str
    template_name: str
    layout_template: str
    view_path: str
    requires_auth: bool
    is_dynamic: bool
    meta_json: dict


def _iter_patterns(patterns: Iterable, prefix: str = "", namespace_parts: list[str] | None = None):
    namespace_parts = namespace_parts or []
    for entry in patterns:
        if isinstance(entry, URLPattern):
            route = f"{prefix}{entry.pattern}"
            callback = entry.callback
            view_class = getattr(callback, "view_class", None)
            view_module = getattr(view_class, "__module__", None) or getattr(callback, "__module__", "")
            view_name = getattr(view_class, "__name__", None) or getattr(callback, "__name__", "")
            full_url_name = ":".join([*namespace_parts, entry.name]) if entry.name else ""
            yield URLPatternDescriptor(
                route=route,
                url_name=full_url_name,
                callback=callback,
                view_path=".".join([part for part in [view_module, view_name] if part]),
                lookup_str=getattr(entry, "lookup_str", ""),
            )
        elif isinstance(entry, URLResolver):
            child_ns = namespace_parts + ([entry.namespace] if entry.namespace else [])
            yield from _iter_patterns(entry.url_patterns, prefix=f"{prefix}{entry.pattern}", namespace_parts=child_ns)


@dataclass(slots=True)
class URLPatternDescriptor:
    route: str
    url_name: str
    callback: object
    view_path: str
    lookup_str: str


def _normalize_route(route: str) -> str:
    route_str = str(route)
    route_str = route_str.replace("^", "").replace("$", "")
    route_str = route_str.replace("(?P<", "<").replace(">[^/]+)", ">")
    route_str = route_str.lstrip("/")
    return f"/{route_str}" if route_str else "/"


def _discover_template_name(callback) -> str:
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        return getattr(view_class, "template_name", "") or ""
    closure = getattr(callback, "__closure__", None) or []
    for cell in closure:
        value = getattr(cell, "cell_contents", None)
        if isinstance(value, str) and value.endswith(".html"):
            return value
    return ""


def _discover_layout_template(template_name: str) -> str:
    if not template_name:
        return ""
    try:
        module = import_module("frontend.templates")
        _ = module
    except Exception:
        pass
    if "dashboard" in template_name or template_name.startswith("web_constructor/"):
        return "layouts/base_dashboard.html"
    if "testing" in template_name:
        return "layouts/base_testing.html"
    return "layouts/base_public.html"


def _requires_auth(callback) -> bool:
    view_class = getattr(callback, "view_class", None)
    if getattr(callback, "login_required", False):
        return True
    if view_class and any(base.__name__ == "LoginRequiredMixin" for base in view_class.__mro__):
        return True
    closure = getattr(callback, "__closure__", None) or []
    for cell in closure:
        cell_value = getattr(cell, "cell_contents", None)
        if callable(cell_value) and getattr(cell_value, "__name__", "") == "test_func":
            return True
    return False


def discover_project_pages() -> list[DiscoveredRoute]:
    resolver = get_resolver()
    discovered: list[DiscoveredRoute] = []
    skip_prefixes = ("/admin/", "/i18n/", "/health/", "/oauth/", "/telegram/webhook/", "/constructor/")
    for entry in _iter_patterns(resolver.url_patterns):
        route_path = _normalize_route(entry.route)
        if any(route_path.startswith(prefix) for prefix in skip_prefixes):
            continue
        route_key = slugify((entry.url_name or route_path).replace(":", "-"))[:255] or slugify(route_path)[:255]
        template_name = _discover_template_name(entry.callback)
        layout_template = _discover_layout_template(template_name)
        title = (entry.url_name or route_path.strip("/") or "home").replace(":", " / ").replace("_", " ").title()
        discovered.append(
            DiscoveredRoute(
                route_key=route_key,
                title=title,
                route_path=route_path,
                url_name=entry.url_name,
                template_name=template_name,
                layout_template=layout_template,
                view_path=entry.view_path or entry.lookup_str,
                requires_auth=_requires_auth(entry.callback),
                is_dynamic="<" in route_path,
                meta_json={"source": "urlconf"},
            )
        )
    return sorted(discovered, key=lambda item: (item.route_path != "/", item.route_path))


def sync_editable_pages() -> list[EditablePage]:
    pages: list[EditablePage] = []
    for index, route in enumerate(discover_project_pages(), start=1):
        page, _created = EditablePage.objects.update_or_create(
            route_key=route.route_key,
            defaults={
                "title": route.title,
                "url_name": route.url_name,
                "route_path": route.route_path,
                "template_name": route.template_name,
                "layout_template": route.layout_template,
                "view_path": route.view_path,
                "source_type": EditablePage.SourceType.URLCONF,
                "requires_auth": route.requires_auth,
                "is_dynamic": route.is_dynamic,
                "is_active": True,
                "sort_order": index,
                "meta_json": route.meta_json,
            },
        )
        pages.append(page)
        ensure_default_areas(page)
    return pages


def ensure_default_areas(page: EditablePage) -> None:
    existing = set(page.areas.values_list("area_key", flat=True))
    for spec in AUTO_AREA_SPECS:
        if spec["area_key"] in existing:
            continue
        EditableArea.objects.create(
            page=page,
            area_key=spec["area_key"],
            label=spec["label"],
            area_type=spec["area_type"],
            sort_order=spec["sort_order"],
            default_content={"source": "auto-generated"},
            default_styles={},
            meta_json={"generated": True},
        )
