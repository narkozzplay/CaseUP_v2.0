from __future__ import annotations

from django.shortcuts import render
from django.urls import reverse


HTML_NOT_FOUND_SKIP_PREFIXES = (
    "/static/",
    "/media/",
)


def _is_english(request) -> bool:
    return str(getattr(request, "LANGUAGE_CODE", "ru") or "ru").lower().startswith("en")


def _build_not_found_context(request) -> dict[str, object]:
    is_authenticated = bool(getattr(request.user, "is_authenticated", False))
    is_english = _is_english(request)

    if is_english:
        title = "Page not found"
        description = "Oops, this page was not found or is no longer available. Check the address or return to a working section of CaseUP."
        if is_authenticated:
            primary_label = "Back to dashboard"
            secondary_label = "Go to home"
        else:
            primary_label = "Go to home"
            secondary_label = "Login via Steam"
    else:
        title = "Страница не найдена"
        description = "Упс, страница не найдена или больше недоступна. Проверь адрес или вернись в рабочий раздел CaseUP."
        if is_authenticated:
            primary_label = "Вернуться в dashboard"
            secondary_label = "На главную"
        else:
            primary_label = "На главную"
            secondary_label = "Войти через Steam"

    primary_url = reverse("frontend:dashboard_preview") if is_authenticated else reverse("frontend:home")
    secondary_url = reverse("frontend:home") if is_authenticated else reverse("accounts:login")

    return {
        "error_layout_template": "layouts/base_dashboard.html" if is_authenticated else "layouts/base_public.html",
        "error_is_dashboard": is_authenticated,
        "error_code": 404,
        "error_title": title,
        "error_description": description,
        "error_primary_url": primary_url,
        "error_primary_label": primary_label,
        "error_secondary_url": secondary_url,
        "error_secondary_label": secondary_label,
    }


def render_not_found_page(request):
    response = render(
        request,
        "errors/not_found.html",
        _build_not_found_context(request),
        status=404,
    )
    setattr(response, "_caseup_custom_404_rendered", True)
    return response