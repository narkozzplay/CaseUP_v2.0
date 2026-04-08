from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.profiles.rbac import get_user_role

from .dashboard_shell import DASHBOARD_RELOAD_URL_HEADER, is_dashboard_partial_request, render_dashboard_page
from .forms import SiteConfigurationForm, ThemeActivateForm
from .services import (
    bootstrap_versioning,
    build_page_title,
    build_theme_update_headers,
    get_available_themes,
    get_control_center_summary,
    get_current_project_version,
    get_module_flag_release_map,
    get_module_release_matrix,
    get_module_state_map,
    get_module_state_views,
    get_external_service_health,
    get_runtime_environment,
    get_site_configuration,
    preview_module_update,
    set_active_theme,
)


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
    bootstrap_versioning()
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
    bootstrap_versioning()
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
        "themes": get_available_themes(project_version=current_project_version),
    }
    return render_dashboard_page(request, full_template="system/versioning.html", partial_template="system/partials/versioning_content.html", context=context, page_title=build_page_title("Versions & Themes"), active_menu_key="system_versioning")



@login_required
@require_POST
def activate_theme_page(request):
    bootstrap_versioning()
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
