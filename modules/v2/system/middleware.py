from __future__ import annotations

from django.shortcuts import render
from django.urls import Resolver404, resolve, reverse
from django.utils.translation import get_language_from_path

from modules.v2.profiles.rbac import get_user_role

from .services import (
    get_module_disabled_note,
    get_site_configuration,
    is_module_enabled,
    resolve_module_for_namespace,
)


def _normalize_path(path: str | None) -> str:
    normalized = path or "/"
    language = get_language_from_path(normalized)
    if not language:
        return normalized

    prefix = f"/{language}"
    if normalized == prefix:
        return "/"
    if normalized.startswith(prefix + "/"):
        stripped = normalized[len(prefix):]
        return stripped or "/"
    return normalized


class MaintenanceModeMiddleware:
    ALLOW_PREFIXES = (
        "/static/",
        "/media/",
        "/health/",
        "/i18n/",
        "/admin/",
        "/auth/login/",
        "/auth/logout/",
        "/telegram/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config = get_site_configuration()
        if not config.maintenance_mode_enabled:
            return self.get_response(request)

        path = _normalize_path(request.path_info)
        if path.startswith(self.ALLOW_PREFIXES):
            return self.get_response(request)

        access = get_user_role(request.user)
        if request.user.is_staff or request.user.is_superuser or access.is_admin_plus:
            return self.get_response(request)

        context = {
            "site_settings": config,
            "maintenance_message": config.maintenance_message,
        }
        return render(request, "system/maintenance.html", context, status=503)


class ModuleAccessMiddleware:
    ALWAYS_ALLOWED_NAMESPACES = {
        "tg_notifier",
        "tg_notifier_public",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)

        if match.namespace in self.ALWAYS_ALLOWED_NAMESPACES:
            return self.get_response(request)

        module = resolve_module_for_namespace(match.namespace)
        if module is not None and not is_module_enabled(module.key):
            access = get_user_role(request.user)
            normalized_path = _normalize_path(request.path_info)
            if normalized_path.startswith("/control-center/") and access.is_admin_plus:
                return self.get_response(request)
            is_authenticated = bool(getattr(request.user, "is_authenticated", False))
            dashboard_url = reverse("frontend:dashboard_preview") if is_authenticated else reverse("frontend:home")
            back_url = (request.META.get("HTTP_REFERER") or "").strip() or dashboard_url
            module_reason = get_module_disabled_note(module.key)
            response = render(
                request,
                "system/module_disabled.html",
                {
                    "module_disabled_layout_template": "layouts/base_dashboard.html" if is_authenticated else "layouts/base_public.html",
                    "module_disabled_is_dashboard": is_authenticated,
                    "module_disabled_module_label": module.label or module.key,
                    "module_disabled_reason": module_reason,
                    "module_disabled_dashboard_url": dashboard_url,
                    "module_disabled_back_url": back_url,
                },
                status=404,
            )
            setattr(response, "_caseup_skip_global_404", True)
            return response

        return self.get_response(request)
