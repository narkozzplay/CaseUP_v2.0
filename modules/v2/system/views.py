from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.profiles.rbac import get_user_role

from .dashboard_shell import DASHBOARD_RELOAD_URL_HEADER, is_dashboard_partial_request, render_dashboard_page
from .forms import SiteConfigurationForm, ThemeActivateForm
from .services import (
    build_page_title,
    build_theme_update_headers,
    disable_runtime_module,
    get_available_themes,
    get_control_center_summary,
    get_current_project_version,
    get_module_flag_release_map,
    get_module_release_matrix,
    get_module_state_map,
    get_module_state_views,
    get_external_service_health,
    get_runtime_environment,
    get_runtime_module_admin_rows,
    get_site_configuration,
    sync_runtime_modules_from_manifests,
    update_module_states,
    preview_module_update,
    set_active_theme,
)
from .module_registry import MODULE_REGISTRY


def _build_settings_context(*, role_access, config, form, current_state_map, preview=None, preview_state_map=None):
    resolved_preview_state_map = preview_state_map or current_state_map
    current_project_version = config.current_project_version or get_current_project_version()
    runtime_environment = get_runtime_environment()
    module_rows = get_module_state_views(state_map=resolved_preview_state_map)
    release_map = get_module_flag_release_map(project_version=current_project_version, environment=runtime_environment)
    module_cards = [{"row": row, "release": release_map[row.key], "field": form[f"module__{row.key}"]} for row in module_rows]
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
        "integration_health": get_external_service_health(),
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
            config = form.save()
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
def versioning_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Version control доступен только admin+.")
    current_project_version = get_current_project_version()
    context = {
        "role_access": role_access,
        "config": get_site_configuration(),
        "summary": get_control_center_summary(),
        "runtime_environment": get_runtime_environment(),
        "current_project_version": current_project_version,
        "module_release_rows": get_module_release_matrix(project_version=current_project_version),
        "runtime_module_rows": get_runtime_module_admin_rows(project_version=current_project_version),
        "themes": get_available_themes(project_version=current_project_version),
    }
    return render_dashboard_page(request, full_template="system/versioning.html", partial_template="system/partials/versioning_content.html", context=context, page_title=build_page_title("Versions & Themes"), active_menu_key="system_versioning")



@login_required
@require_POST
def activate_theme_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Theme activation доступна только admin+.")

    form = ThemeActivateForm(request.POST, current_version=get_current_project_version())
    next_url = request.POST.get("next_url") or request.META.get("HTTP_REFERER") or reverse("system_config:versioning")
    if not form.is_valid():
        messages.error(request, "Не удалось активировать тему. Проверь совместимость темы с текущей версией проекта.")
        return redirect(next_url)

    theme = form.cleaned_data["theme"]
    previous_theme = get_site_configuration().active_theme
    try:
        config = set_active_theme(theme)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect(next_url)

    messages.success(request, f"Тема '{theme.name}' активирована.")
    if is_dashboard_partial_request(request):
        current_project_version = get_current_project_version()
        context = {
            "role_access": role_access,
            "config": config,
            "summary": get_control_center_summary(),
            "runtime_environment": get_runtime_environment(),
            "current_project_version": current_project_version,
            "module_release_rows": get_module_release_matrix(project_version=current_project_version),
            "runtime_module_rows": get_runtime_module_admin_rows(project_version=current_project_version),
            "themes": get_available_themes(project_version=current_project_version),
        }
        response_headers = build_theme_update_headers(previous_theme=previous_theme, next_theme=config.active_theme)
        if response_headers.get("X-Dashboard-Theme-Mode") == "reload":
            response_headers[DASHBOARD_RELOAD_URL_HEADER] = reverse("system_config:versioning")
        return render_dashboard_page(
            request,
            full_template="system/versioning.html",
            partial_template="system/partials/versioning_content.html",
            context=context,
            page_title=build_page_title("Versions & Themes"),
            active_menu_key="system_versioning",
            current_url=reverse("system_config:versioning"),
            response_headers=response_headers,
        )
    return redirect(next_url)


@login_required
@require_POST
def sync_runtime_modules_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Runtime module sync доступен только admin+.")

    result = sync_runtime_modules_from_manifests()
    message = (
        f"Runtime modules sync: найдено {result.total}, создано {result.created}, "
        f"обновлено {result.updated}, без изменений {result.skipped}, ошибок {result.failed}."
    )
    if result.failed:
        messages.warning(request, message + " " + " | ".join(result.errors))
    else:
        messages.success(request, message)
    return redirect("system_config:versioning")


@login_required
@require_POST
def disable_runtime_module_page(request, module_key: str):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Runtime module disable доступен только admin+.")

    try:
        updated = disable_runtime_module(module_key=module_key)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("system_config:versioning")

    if updated:
        messages.success(request, f"Runtime module '{module_key}' отключён.")
    else:
        messages.info(request, f"Runtime module '{module_key}' уже отключён или не найден.")
    return redirect("system_config:versioning")


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
