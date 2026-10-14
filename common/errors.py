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

def _build_access_denied_context(request, *, detail: str = "") -> dict[str, object]:
    is_authenticated = bool(getattr(request.user, "is_authenticated", False))
    is_english = _is_english(request)

    if is_english:
        title = "Access to this page is closed"
        description = "You don't have permission to open this section. If you think this is a mistake, contact the CaseUP administration."
        footer_label = "Permissions"
        if is_authenticated:
            primary_label = "Back to dashboard"
            secondary_label = "My profile"
        else:
            primary_label = "Login via Steam"
            secondary_label = "Go to home"
    else:
        title = "Доступ к странице закрыт"
        description = "У тебя нет прав на этот раздел. Если это ошибка, напиши администрации CaseUP."
        footer_label = "Права доступа"
        if is_authenticated:
            primary_label = "Вернуться в dashboard"
            secondary_label = "Мой профиль"
        else:
            primary_label = "Войти через Steam"
            secondary_label = "На главную"

    if is_authenticated:
        primary_url = reverse("frontend:dashboard_preview")
        secondary_url = reverse("profiles:profile")
    else:
        primary_url = reverse("accounts:login")
        secondary_url = reverse("frontend:home")

    return {
        "error_layout_template": "layouts/base_dashboard.html" if is_authenticated else "layouts/base_public.html",
        "error_is_dashboard": is_authenticated,
        "error_code": 403,
        "ad_kind": "permission",
        "ad_title": title,
        "ad_description": description,
        "ad_detail": detail,
        "ad_footer_label": footer_label,
        "ad_primary_url": primary_url,
        "ad_primary_label": primary_label,
        "ad_secondary_url": secondary_url,
        "ad_secondary_label": secondary_label,
    }


def render_access_denied_page(request, *, detail: str = ""):
    response = render(
        request,
        "errors/access_denied.html",
        _build_access_denied_context(request, detail=detail),
        status=403,
    )
    setattr(response, "_caseup_custom_403_rendered", True)
    return response


def extract_plain_forbidden_detail(response) -> str | None:
    """
    Return the message of a bare-text 403 (HttpResponseForbidden("...")), or None
    when the response is already a real page, JSON, or streamed.
    """
    if getattr(response, "streaming", False):
        return None
    content_type = str(response.get("Content-Type", "") or "").lower()
    if content_type and not content_type.startswith("text/html"):
        return None
    try:
        body = response.content.decode(response.charset or "utf-8", errors="replace").strip()
    except Exception:
        return None
    if "<" in body or len(body) > 500:
        return None
    return body
