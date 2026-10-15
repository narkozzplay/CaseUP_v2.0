from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import PriceAutoUpdateRuleForm
from .models import PriceAutoUpdateRule, PriceAutoUpdateRunLog
from .services import clear_rule_schedule, rebuild_rule_schedule
from .tasks import run_price_auto_update_rule_task


def _control_center_access(user) -> bool:
    return get_user_role(user).is_admin_plus


def _build_control_center_context(*, role_access, form, edit_rule=None):
    from modules.v2.steam_catalog.services.price_queue import coverage

    rules = list(PriceAutoUpdateRule.objects.order_by("category"))
    recent_logs = (
        PriceAutoUpdateRunLog.objects
        .select_related("rule")
        .order_by("-started_at", "-id")[:20]
    )
    last_success = (
        PriceAutoUpdateRunLog.objects.filter(status=PriceAutoUpdateRule.Status.SUCCESS)
        .order_by("-finished_at")
        .values_list("finished_at", flat=True)
        .first()
    )
    free_categories = [code for code, _label in form.fields["category"].choices if code]
    return {
        "cc_tab": "celery",
        "role_access": role_access,
        "form": form,
        "rules": rules,
        "recent_logs": recent_logs,
        "current_rule": edit_rule,
        "queue": coverage(),
        "can_add_rule": bool(edit_rule) or bool(free_categories),
        "summary": {
            "total": len(rules),
            "enabled": sum(1 for rule in rules if rule.is_enabled),
            "failing": sum(1 for rule in rules if rule.last_status == PriceAutoUpdateRule.Status.ERROR),
            "last_success_at": last_success,
        },
    }


def _render_control_center(request, *, role_access, form, edit_rule=None, current_url=""):
    return render_dashboard_page(
        request,
        full_template="celery_tasks/control_center.html",
        partial_template="celery_tasks/partials/control_center_content.html",
        context=_build_control_center_context(role_access=role_access, form=form, edit_rule=edit_rule),
        page_title=build_page_title("Автообновление цен"),
        active_menu_key="celery_tasks",
        current_url=current_url,
    )


@login_required
def control_center(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Celery Tasks доступны только admin+.")

    edit_rule = None
    edit_id = request.GET.get("edit")
    if edit_id:
        edit_rule = get_object_or_404(PriceAutoUpdateRule, pk=edit_id)

    if request.method == "POST":
        submitted_rule_id = request.POST.get("rule_id")
        instance = None
        previous_enabled = False
        previous_signature = None

        if submitted_rule_id:
            instance = get_object_or_404(PriceAutoUpdateRule, pk=submitted_rule_id)
            previous_enabled = instance.is_enabled
            previous_signature = (
                instance.category,
                instance.schedule_mode,
                instance.interval_minutes,
                instance.start_at,
                instance.hourly_minute,
            )

        form = PriceAutoUpdateRuleForm(request.POST, instance=instance)
        if form.is_valid():
            rule = form.save()

            current_signature = (
                rule.category,
                rule.schedule_mode,
                rule.interval_minutes,
                rule.start_at,
                rule.hourly_minute,
            )
            schedule_changed = previous_signature != current_signature

            if rule.is_enabled and (
                not previous_enabled
                or schedule_changed
                or rule.next_run_at is None
            ):
                rebuild_rule_schedule(rule)
            elif not rule.is_enabled:
                clear_rule_schedule(rule)

            messages.success(request, f"Правило сохранено: {rule.category_label}")
            if request.headers.get("X-Dashboard-Partial") == "1":
                return _render_control_center(
                    request,
                    role_access=role_access,
                    form=PriceAutoUpdateRuleForm(),
                    edit_rule=None,
                    current_url=request.path,
                )
            return redirect("celery_tasks:control_center")
    else:
        form = PriceAutoUpdateRuleForm(instance=edit_rule)

    return _render_control_center(request, role_access=role_access, form=form, edit_rule=edit_rule)


@login_required
def toggle_rule(request, rule_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Method not allowed.")

    if not _control_center_access(request.user):
        return HttpResponseForbidden("Celery Tasks доступны только admin+.")

    rule = get_object_or_404(PriceAutoUpdateRule, pk=rule_id)
    rule.is_enabled = not rule.is_enabled
    rule.save(update_fields=["is_enabled", "updated_at"])

    if rule.is_enabled:
        rebuild_rule_schedule(rule)
        messages.success(request, f"Автообновление включено: {rule.category_label}")
    else:
        clear_rule_schedule(rule)
        messages.success(request, f"Автообновление выключено: {rule.category_label}")

    if request.headers.get("X-Dashboard-Partial") == "1":
        return _render_control_center(
            request,
            role_access=get_user_role(request.user),
            form=PriceAutoUpdateRuleForm(),
            edit_rule=None,
            current_url=reverse("celery_tasks:control_center"),
        )
    return redirect("celery_tasks:control_center")


@login_required
def run_rule_now(request, rule_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Method not allowed.")

    if not _control_center_access(request.user):
        return HttpResponseForbidden("Celery Tasks доступны только admin+.")

    rule = get_object_or_404(PriceAutoUpdateRule, pk=rule_id)
    run_price_auto_update_rule_task.delay(rule.id, triggered_by="manual")
    messages.success(request, f"Обновление цен запущено: {rule.category_label}. Результат появится в истории через пару минут.")

    if request.headers.get("X-Dashboard-Partial") == "1":
        return _render_control_center(
            request,
            role_access=get_user_role(request.user),
            form=PriceAutoUpdateRuleForm(),
            edit_rule=None,
            current_url=reverse("celery_tasks:control_center"),
        )
    return redirect("celery_tasks:control_center")
