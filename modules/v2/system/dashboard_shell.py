from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import render

DASHBOARD_PARTIAL_HEADER = "X-Dashboard-Partial"
DASHBOARD_PARTIAL_VALUE = "1"
DASHBOARD_CURRENT_URL_HEADER = "X-Dashboard-Current-Url"
DASHBOARD_THEME_MODE_HEADER = "X-Dashboard-Theme-Mode"
DASHBOARD_THEME_CSS_URL_HEADER = "X-Dashboard-Theme-Css-Url"
DASHBOARD_THEME_KEY_HEADER = "X-Dashboard-Theme-Key"
DASHBOARD_RELOAD_URL_HEADER = "X-Dashboard-Reload-Url"


def is_dashboard_partial_request(request) -> bool:
    return request.headers.get(DASHBOARD_PARTIAL_HEADER) == DASHBOARD_PARTIAL_VALUE


def render_dashboard_page(
    request,
    *,
    full_template: str,
    partial_template: str,
    context: dict[str, object] | None = None,
    page_title: str = "",
    active_menu_key: str = "",
    current_url: str = "",
    response_headers: dict[str, str] | None = None,
):
    payload = dict(context or {})
    payload.update(
        {
            "dashboard_page_partial_template": partial_template,
            "dashboard_page_title": page_title,
            "dashboard_active_menu_key": active_menu_key,
            "dashboard_current_url": current_url or request.get_full_path(),
        }
    )
    if is_dashboard_partial_request(request):
        response = render(request, "components/dashboard_content_frame.html", payload)
        response["X-Dashboard-Partial"] = DASHBOARD_PARTIAL_VALUE
        if page_title:
            response["X-Dashboard-Page-Title"] = page_title
        if active_menu_key:
            response["X-Dashboard-Active-Menu"] = active_menu_key
        response[DASHBOARD_CURRENT_URL_HEADER] = payload["dashboard_current_url"]
        for header_name, header_value in (response_headers or {}).items():
            if header_value:
                response[header_name] = header_value
        return response
    response = render(request, full_template, payload)
    for header_name, header_value in (response_headers or {}).items():
        if header_value:
            response[header_name] = header_value
    return response
