from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from modules.v2.profiles.rbac import get_user_role

from .dashboard_shell import render_dashboard_page
from .forms import SiteConfigurationForm
from .services import (
    get_control_center_summary,
    get_module_state_map,
    get_module_state_views,
    get_site_configuration,
    preview_module_update,
)


def _build_settings_context(*, role_access, config, form, current_state_map, preview=None, preview_state_map=None):
    resolved_preview_state_map = preview_state_map or current_state_map
    module_rows = get_module_state_views(state_map=resolved_preview_state_map)
    module_cards = [
        {
            "row": row,
            "field": form[f"module__{row.key}"],
        }
        for row in module_rows
    ]
    return {
        "role_access": role_access,
        "config": config,
        "form": form,
        "module_cards": module_cards,
        "module_rows": module_rows,
        "summary": get_control_center_summary(config=config, state_map=resolved_preview_state_map),
        "module_preview": preview,
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
            preview = preview_module_update(current_state_map, form.get_module_states())
            form.save()

            messages.success(request, "Системные настройки сохранены.")
            if preview.cascaded_disabled_labels:
                messages.warning(
                    request,
                    "Каскадно выключены зависимые модули: "
                    + ", ".join(preview.cascaded_disabled_labels)
                    + ".",
                )

            context = _build_settings_context(
                role_access=role_access,
                config=config,
                form=SiteConfigurationForm(instance=config),
                current_state_map=get_module_state_map(),
                preview=None,
                preview_state_map=get_module_state_map(),
            )
            if request.headers.get("X-Dashboard-Partial") == "1":
                return render_dashboard_page(
                    request,
                    full_template="system/settings.html",
                    partial_template="system/partials/settings_content.html",
                    context=context,
                    page_title="System Settings | CaseUP",
                    active_menu_key="system_settings",
                    current_url=request.path,
                )
            return redirect("system_config:settings")
    else:
        form = SiteConfigurationForm(instance=config)
        preview = None
        preview_state_map = current_state_map

    context = _build_settings_context(
        role_access=role_access,
        config=config,
        form=form,
        current_state_map=current_state_map,
        preview=preview,
        preview_state_map=preview_state_map,
    )
    return render_dashboard_page(
        request,
        full_template="system/settings.html",
        partial_template="system/partials/settings_content.html",
        context=context,
        page_title="System Settings | CaseUP",
        active_menu_key="system_settings",
    )
