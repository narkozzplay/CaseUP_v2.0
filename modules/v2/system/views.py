from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.profiles.rbac import get_user_role

from .dashboard_shell import DASHBOARD_RELOAD_URL_HEADER, is_dashboard_partial_request, render_dashboard_page
from .forms import SiteConfigurationForm
from .services import (
    build_page_title,
    build_theme_update_headers,
    get_control_center_summary,
    get_current_project_version,
    get_module_flag_release_map,
    get_module_state_map,
    get_module_state_views,
    get_runtime_environment,
    get_site_configuration,
    update_module_states,
    preview_module_update,
)
from .module_registry import MODULE_REGISTRY
from .settings_tools import diff_settings, export_settings_payload, get_cached_integration_health, snapshot_settings

SETTINGS_HISTORY_ACTIONS = ("settings_update", "module_toggle", "site_maintenance", "site_registration", "cache_clear")


def _settings_history(limit: int = 10):
    from modules.v2.control_center.models import AdminAuditLog

    return AdminAuditLog.objects.filter(action__in=SETTINGS_HISTORY_ACTIONS).select_related("actor")[:limit]


def _log_settings_change(request, before: dict[str, object]) -> None:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    changes = diff_settings(before, snapshot_settings())
    if not changes:
        return
    labels = ", ".join(change["label"] for change in changes[:4])
    if len(changes) > 4:
        labels += f" и ещё {len(changes) - 4}"
    log_admin_action(
        actor=request.user,
        action=AuditAction.SETTINGS_UPDATE,
        summary=labels,
        payload={"changes": changes},
        request=request,
    )


def _build_settings_context(*, role_access, config, form, current_state_map, preview=None, preview_state_map=None):
    resolved_preview_state_map = preview_state_map or current_state_map
    current_project_version = config.current_project_version or get_current_project_version()
    runtime_environment = get_runtime_environment()
    module_rows = get_module_state_views(state_map=resolved_preview_state_map)
    release_map = get_module_flag_release_map(project_version=current_project_version, environment=runtime_environment)
    module_cards = [{"row": row, "release": release_map[row.key], "field": form[f"module__{row.key}"]} for row in module_rows]
    integration_health = get_cached_integration_health()
    return {
        "role_access": role_access,
        "config": config,
        "form": form,
        "module_cards": module_cards,
        "module_rows": module_rows,
        "summary": get_control_center_summary(config=config, state_map=resolved_preview_state_map),
        "runtime_environment": runtime_environment,
        "current_project_version": current_project_version,
        "module_preview": preview,
        "integration_health": integration_health["items"],
        "integration_checked_at": integration_health["checked_at"],
        "settings_history": _settings_history(),
        "modules_enabled_count": sum(1 for row in module_rows if row.is_enabled),
        "modules_total_count": len(module_rows),
        "cc_tab": "settings",
        "masked_integration_secrets": getattr(form, "masked_integration_secrets", {}),
    }


@login_required
def settings_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("System configuration доступна только admin+.")
    config = get_site_configuration()
    current_state_map = get_module_state_map()
    if request.method == "POST":
        form = SiteConfigurationForm(request.POST, instance=config)
        proposed_state_map = form.get_submitted_module_states()
        try:
            preview = preview_module_update(current_state_map, proposed_state_map)
            preview_state_map = preview.merged_state_map
        except ValidationError:
            preview = None
            preview_state_map = proposed_state_map
        if form.is_valid():
            previous_theme = config.active_theme
            preview = preview_module_update(current_state_map, form.get_module_states())
            before = snapshot_settings()
            config = form.save()
            _log_settings_change(request, before)
            messages.success(request, "Системные настройки сохранены.")
            if preview.cascaded_disabled_labels:
                messages.warning(request, "Каскадно выключены зависимые модули: " + ", ".join(preview.cascaded_disabled_labels) + ".")
            if is_dashboard_partial_request(request):
                refreshed_form = SiteConfigurationForm(instance=config)
                refreshed_state_map = get_module_state_map()
                context = _build_settings_context(
                    role_access=role_access,
                    config=config,
                    form=refreshed_form,
                    current_state_map=refreshed_state_map,
                    preview=None,
                    preview_state_map=refreshed_state_map,
                )
                response_headers = build_theme_update_headers(previous_theme=previous_theme, next_theme=config.active_theme)
                if response_headers.get("X-Dashboard-Theme-Mode") == "reload":
                    response_headers[DASHBOARD_RELOAD_URL_HEADER] = reverse("system_config:settings")
                return render_dashboard_page(
                    request,
                    full_template="system/settings.html",
                    partial_template="system/partials/settings_content.html",
                    context=context,
                    page_title=build_page_title("System Settings"),
                    active_menu_key="system_settings",
                    current_url=reverse("system_config:settings"),
                    response_headers=response_headers,
                )
            return redirect("system_config:settings")
    else:
        form = SiteConfigurationForm(instance=config)
        preview = None
        preview_state_map = current_state_map
    context = _build_settings_context(role_access=role_access, config=config, form=form, current_state_map=current_state_map, preview=preview, preview_state_map=preview_state_map)
    return render_dashboard_page(request, full_template="system/settings.html", partial_template="system/partials/settings_content.html", context=context, page_title=build_page_title("System Settings"), active_menu_key="system_settings")


@login_required
@require_POST
def toggle_module_state_page(request, module_key: str):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if module_key not in MODULE_REGISTRY:
        return JsonResponse({"ok": False, "error": "unknown_module"}, status=404)

    submitted = str(request.POST.get("enabled", "")).strip().lower()
    enabled = submitted in {"1", "true", "on", "yes"}
    try:
        update_module_states({module_key: enabled})
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    state_map = get_module_state_map()
    final_enabled = bool(state_map.get(module_key, MODULE_REGISTRY[module_key].default_enabled))
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    log_admin_action(
        actor=request.user,
        action=AuditAction.MODULE_TOGGLE,
        summary=f"{MODULE_REGISTRY[module_key].label}: {'on' if final_enabled else 'off'}",
        payload={"module": module_key, "enabled": final_enabled},
        request=request,
    )
    return JsonResponse(
        {
            "ok": True,
            "module_key": module_key,
            "enabled": final_enabled,
            "message": f"Module '{MODULE_REGISTRY[module_key].label}' {'enabled' if final_enabled else 'disabled'}.",
        }
    )


@login_required
@require_POST
def check_integrations_page(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("System configuration доступна только admin+.")
    get_cached_integration_health(refresh=True)
    messages.success(request, "Интеграции проверены.")
    return redirect(reverse("system_config:settings") + "#integrations")


@login_required
@require_POST
def clear_cache_page(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("System configuration доступна только admin+.")
    from django.core.cache import cache

    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    cache.clear()
    log_admin_action(actor=request.user, action=AuditAction.CACHE_CLEAR, summary="default cache", request=request)
    messages.success(request, "Кэш очищен.")
    return redirect("system_config:settings")


@login_required
def export_settings_page(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("System configuration доступна только admin+.")
    from django.utils import timezone

    response = JsonResponse(export_settings_payload(), json_dumps_params={"ensure_ascii": False, "indent": 2})
    response["Content-Disposition"] = f'attachment; filename="caseup-settings-{timezone.localdate():%Y%m%d}.json"'
    return response


@login_required
def maintenance_preview_page(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("System configuration доступна только admin+.")
    config = get_site_configuration()
    message = (request.GET.get("message") or "").strip() or config.maintenance_message
    return render(request, "system/maintenance.html", {"site_settings": config, "maintenance_message": message, "maintenance_preview": True})
