from __future__ import annotations

import csv
import ipaddress
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from modules.v2.accounts.models import TwoFactorAuth
from modules.v2.antifraud.services import user_ip_history
from modules.v2.accounts.sessions import active_sessions
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole, VerificationRequest
from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.services.presence import ONLINE_THRESHOLD_MINUTES, is_profile_online
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title, get_control_center_summary, get_site_configuration

from .models import AUDIT_ACTION_LABELS_RU, AdminAuditLog, AuditAction, UserBan
from .services import health
from .services.audit import log_admin_action
from .services.stats import ROLE_LABELS_RU, STAFF_ROLES, build_overview
from .services.users import (
    BAN_DURATION_CHOICES,
    UserManagementError,
    add_admin_note,
    assignable_roles,
    ban_user,
    can_manage_user,
    change_user_role,
    get_active_ban,
    lift_expired_bans,
    reset_two_factor,
    terminate_sessions,
    unban_user,
)

User = get_user_model()

FULL_TEMPLATE = "control_center/page.html"
USERS_PAGE_SIZE = 30
LEDGER_PAGE_SIZE = 50
PAYMENTS_PAGE_SIZE = 50
AUDIT_PAGE_SIZE = 40
MODERATION_PAGE_SIZE = 40
ACTIVITY_PAGE_SIZE = 50
CSV_EXPORT_LIMIT = 10000

USER_STATUS_FILTERS = (
    ("", "Все"),
    ("active", "Активные"),
    ("banned", "Забаненные"),
    ("online", "Онлайн"),
    ("staff", "Команда"),
    ("new", "Новые (7 дней)"),
)
USER_SORTS = {
    "newest": ("-user__date_joined", "-id"),
    "oldest": ("user__date_joined", "id"),
    "last_seen": ("-last_seen_at", "-id"),
    "balance": ("-balance_coins", "-id"),
    "username": ("user__username", "id"),
}
USER_SORT_LABELS = (
    ("newest", "Сначала новые"),
    ("oldest", "Сначала старые"),
    ("last_seen", "Последняя активность"),
    ("balance", "Баланс"),
    ("username", "Логин"),
)


def _forbidden():
    return HttpResponseForbidden("Control Center доступен только admin+.")


def _admin_access(request):
    access = get_user_role(request.user)
    return access if access.is_admin_plus else None


def _render(request, *, partial: str, context: dict, title: str, tab: str):
    context = {"cc_tab": tab, **context}
    return render_dashboard_page(
        request,
        full_template=FULL_TEMPLATE,
        partial_template=partial,
        context=context,
        page_title=build_page_title(title),
        active_menu_key="control_center",
    )


def _querystring(request, *, drop: tuple[str, ...] = ("page",)) -> str:
    params = request.GET.copy()
    for key in drop:
        params.pop(key, None)
    return params.urlencode()


def _parse_date(raw: str):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_next(request, fallback: str) -> str:
    target = (request.POST.get("next") or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def _csv_response(filename: str) -> tuple[HttpResponse, csv.writer]:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")
    return response, csv.writer(response, delimiter=";")


# --------------------------------------------------------------------------- overview


@login_required
def overview(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    lift_expired_bans()

    data = build_overview()
    kpis = data["kpis"]
    quick = health.quick_checks()
    summary = get_control_center_summary()

    attention = []
    if summary["maintenance_mode_enabled"]:
        attention.append({"level": "warn", "title": "Включён режим обслуживания", "text": "Сайт закрыт для обычных пользователей.", "url_name": "control_center:health"})
    if kpis["verifications_pending"]:
        attention.append({"level": "info", "title": f"Заявок на верификацию: {kpis['verifications_pending']}", "text": "Ждут решения модератора.", "url_name": "profiles:verification_requests"})
    if kpis["catalog_review"]:
        attention.append({"level": "info", "title": f"Предметов на проверке: {kpis['catalog_review']}", "text": "Новые позиции из Steam Market.", "url_name": "control_center:moderation"})
    for check in quick:
        if check.status in {health.FAIL, health.WARN}:
            attention.append({"level": "fail" if check.status == health.FAIL else "warn", "title": f"{check.label}: {check.value}", "text": check.hint, "url_name": "control_center:health"})

    context = {
        "role_access": role_access,
        **data,
        "quick_checks": quick,
        "health_status": health.overall_status(quick),
        "attention": attention,
        "summary": summary,
        "build_version": health.read_build_version(),
        "audit_labels": AUDIT_ACTION_LABELS_RU,
    }
    return _render(
        request,
        partial="control_center/partials/overview_content.html",
        context=context,
        title="Control Center",
        tab="overview",
    )


@login_required
@require_POST
def site_toggle(request):
    if _admin_access(request) is None:
        return _forbidden()
    field = (request.POST.get("field") or "").strip()
    enabled = (request.POST.get("enabled") or "").strip() in {"1", "true", "on"}
    config = get_site_configuration()
    if field == "maintenance":
        config.maintenance_mode_enabled = enabled
        config.save(update_fields=["maintenance_mode_enabled", "updated_at"])
        log_admin_action(
            actor=request.user,
            action=AuditAction.SITE_MAINTENANCE,
            summary="Включён" if enabled else "Выключен",
            payload={"enabled": enabled},
            request=request,
        )
        messages.success(request, "Режим обслуживания включён." if enabled else "Режим обслуживания выключен.")
    elif field == "registration":
        config.registration_enabled = enabled
        config.save(update_fields=["registration_enabled", "updated_at"])
        log_admin_action(
            actor=request.user,
            action=AuditAction.SITE_REGISTRATION,
            summary="Открыта" if enabled else "Закрыта",
            payload={"enabled": enabled},
            request=request,
        )
        messages.success(request, "Регистрация открыта." if enabled else "Регистрация закрыта.")
    else:
        messages.error(request, "Неизвестная настройка.")
    return redirect(_safe_next(request, reverse("frontend:admin_panel")))


# --------------------------------------------------------------------------- users


def _filtered_profiles(request):
    q = (request.GET.get("q") or "").strip()
    role = (request.GET.get("role") or "").strip()
    status = (request.GET.get("status") or "").strip()
    sort = (request.GET.get("sort") or "").strip()
    if role not in {code for code, _ in UserRole.choices}:
        role = ""
    if status not in {code for code, _ in USER_STATUS_FILTERS}:
        status = ""
    if sort not in USER_SORTS:
        sort = "newest"

    qs = UserProfile.objects.select_related("user")
    if q:
        query = (
            Q(user__username__icontains=q)
            | Q(user__email__icontains=q)
            | Q(steam_personaname__icontains=q)
            | Q(steam_id__icontains=q)
            | Q(faceit_nickname__icontains=q)
        )
        if q.isdigit():
            query |= Q(user_id=int(q))
        qs = qs.filter(query)
    if role:
        qs = qs.filter(role=role)

    banned_ids = UserBan.objects.active().values("user_id")
    now = timezone.now()
    if status == "active":
        qs = qs.filter(user__is_active=True)
    elif status == "banned":
        qs = qs.filter(Q(user_id__in=banned_ids) | Q(user__is_active=False))
    elif status == "online":
        qs = qs.filter(last_seen_at__gte=now - timedelta(minutes=ONLINE_THRESHOLD_MINUTES))
    elif status == "staff":
        qs = qs.filter(role__in=STAFF_ROLES)
    elif status == "new":
        qs = qs.filter(user__date_joined__gte=now - timedelta(days=7))

    filters = {"q": q, "role": role, "status": status, "sort": sort}
    return qs.order_by(*USER_SORTS[sort]), filters


@login_required
def users_list(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    lift_expired_bans()

    qs, filters = _filtered_profiles(request)
    paginator = Paginator(qs, USERS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    profiles = list(page_obj.object_list)
    active_bans = {
        ban.user_id: ban
        for ban in UserBan.objects.active().filter(user_id__in=[p.user_id for p in profiles])
    }
    rows = []
    for profile in profiles:
        user = profile.user
        rows.append(
            {
                "profile": profile,
                "user": user,
                "display_name": get_profile_display_name(profile, fallback_username=user.username),
                "avatar_url": get_profile_avatar_url(profile),
                "role_label": ROLE_LABELS_RU.get(profile.role, profile.role),
                "is_online": is_profile_online(profile),
                "ban": active_bans.get(user.id),
                "is_blocked": not user.is_active,
                "can_manage": can_manage_user(request.user, user),
            }
        )

    counts = UserProfile.objects.aggregate(
        total=Count("id"),
        staff=Count("id", filter=Q(role__in=STAFF_ROLES)),
        blocked=Count("id", filter=Q(user__is_active=False)),
    )
    context = {
        "role_access": role_access,
        "rows": rows,
        "page_obj": page_obj,
        "result_count": paginator.count,
        "filters": filters,
        "counts": counts,
        "role_options": [(code, ROLE_LABELS_RU[code]) for code, _ in UserRole.choices],
        "status_options": USER_STATUS_FILTERS,
        "sort_options": USER_SORT_LABELS,
        "pagination_query": _querystring(request),
        "export_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/users_content.html", context=context, title="Пользователи · Control Center", tab="users")


@login_required
def users_export(request):
    if _admin_access(request) is None:
        return _forbidden()
    qs, _filters = _filtered_profiles(request)
    response, writer = _csv_response(f"caseup-users-{timezone.localdate():%Y%m%d}.csv")
    writer.writerow(["id", "username", "email", "steam_name", "steam_id", "role", "balance", "is_active", "date_joined", "last_seen_at"])
    for profile in qs[:CSV_EXPORT_LIMIT]:
        user = profile.user
        writer.writerow(
            [
                user.id,
                user.username,
                user.email,
                profile.steam_personaname,
                profile.steam_id or "",
                profile.role,
                profile.balance_coins,
                int(user.is_active),
                timezone.localtime(user.date_joined).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(profile.last_seen_at).strftime("%Y-%m-%d %H:%M") if profile.last_seen_at else "",
            ]
        )
    return response


@login_required
def user_detail(request, user_id: int):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    lift_expired_bans()

    target = get_object_or_404(User, pk=user_id)
    profile, _created = UserProfile.objects.get_or_create(user=target)
    ledger_qs = LedgerEntry.objects.filter(user=target)
    ledger_stats = ledger_qs.aggregate(
        count=Count("id"),
        income=Sum("delta", filter=Q(delta__gt=0)),
        spent=Sum("delta", filter=Q(delta__lt=0)),
    )
    reason_breakdown = (
        ledger_qs.values("reason")
        .annotate(total=Sum("delta"), count=Count("id"))
        .order_by("-count")
    )
    reason_labels = dict(LedgerReason.choices)

    context = {
        "role_access": role_access,
        "target": target,
        "profile": profile,
        "display_name": get_profile_display_name(profile, fallback_username=target.username),
        "avatar_url": get_profile_avatar_url(profile),
        "role_label": ROLE_LABELS_RU.get(profile.role, profile.role),
        "is_online": is_profile_online(profile),
        "active_ban": get_active_ban(target),
        "ban_history": UserBan.objects.filter(user=target).select_related("banned_by", "lifted_by")[:10],
        "can_manage": can_manage_user(request.user, target),
        "is_self": request.user.pk == target.pk,
        "role_choices": [(code, ROLE_LABELS_RU.get(code, label)) for code, label in assignable_roles(request.user)],
        "ban_durations": [(code, label) for code, label, _delta in BAN_DURATION_CHOICES],
        "ledger_stats": ledger_stats,
        "reason_breakdown": [
            {**row, "label": reason_labels.get(row["reason"], row["reason"])} for row in reason_breakdown
        ],
        "recent_ledger": ledger_qs.order_by("-created_at")[:12],
        "audit_entries": AdminAuditLog.objects.filter(target_user=target).select_related("actor")[:15],
        "verification_requests": VerificationRequest.objects.filter(user=target).order_by("-created_at")[:5],
        "target_sessions": list(active_sessions(target)[:10]),
        "target_two_factor": TwoFactorAuth.objects.filter(user=target, confirmed_at__isnull=False).first(),
        "target_ips": user_ip_history(target),
        "target_cases": list(target.multi_account_cases.order_by("-updated_at")[:5]),
    }
    return _render(
        request,
        partial="control_center/partials/user_detail_content.html",
        context=context,
        title=f"{context['display_name']} · Control Center",
        tab="users",
    )


@login_required
@require_POST
def user_action(request, user_id: int):
    if _admin_access(request) is None:
        return _forbidden()
    target = get_object_or_404(User, pk=user_id)
    action = (request.POST.get("action") or "").strip()
    try:
        if action == "ban":
            ban = ban_user(
                actor=request.user,
                target=target,
                reason=request.POST.get("reason", ""),
                duration=request.POST.get("duration", ""),
                request=request,
            )
            until = timezone.localtime(ban.expires_at).strftime("%d.%m.%Y %H:%M") if ban.expires_at else "навсегда"
            messages.success(request, f"{target.username} забанен ({until}).")
        elif action == "unban":
            unban_user(actor=request.user, target=target, request=request)
            messages.success(request, f"{target.username} разбанен.")
        elif action == "role":
            changed = change_user_role(actor=request.user, target=target, role=request.POST.get("role", ""), request=request)
            messages.success(request, "Роль обновлена." if changed else "Роль не изменилась.")
        elif action == "logout":
            removed = terminate_sessions(actor=request.user, target=target, request=request)
            messages.success(request, f"Завершено сессий: {removed}.")
        elif action == "reset_2fa":
            reset_two_factor(actor=request.user, target=target, request=request)
            messages.success(request, f"Двухфакторная защита {target.username} сброшена. При следующем входе пользователь сможет подключить её заново.")
        elif action == "note":
            add_admin_note(actor=request.user, target=target, note=request.POST.get("note", ""), request=request)
            messages.success(request, "Заметка сохранена в журнал.")
        else:
            messages.error(request, "Неизвестное действие.")
    except UserManagementError as exc:
        messages.error(request, str(exc))
    fallback = reverse("control_center:user_detail", args=[target.pk])
    return redirect(_safe_next(request, fallback))


# --------------------------------------------------------------------------- audit


@login_required
def audit_log(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    action = (request.GET.get("action") or "").strip()
    q = (request.GET.get("q") or "").strip()
    qs = AdminAuditLog.objects.select_related("actor", "actor__profile_v2", "target_user", "target_user__profile_v2")
    if action in AuditAction.values:
        qs = qs.filter(action=action)
    else:
        action = ""
    if q:
        qs = qs.filter(
            Q(summary__icontains=q) | Q(actor__username__icontains=q) | Q(target_user__username__icontains=q)
        )
    page_obj = Paginator(qs.order_by("-created_at", "-id"), AUDIT_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": {"action": action, "q": q},
        "action_options": [(code, AUDIT_ACTION_LABELS_RU.get(code, label)) for code, label in AuditAction.choices],
        "action_counts": {
            row["action"]: row["total"]
            for row in AdminAuditLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=30))
            .values("action")
            .annotate(total=Count("id"))
        },
        "pagination_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/audit_content.html", context=context, title="Журнал · Control Center", tab="audit")


# --------------------------------------------------------------------------- ledger


def _filtered_ledger(request):
    reason = (request.GET.get("reason") or "").strip()
    q = (request.GET.get("q") or "").strip()
    direction = (request.GET.get("direction") or "").strip()
    date_from = _parse_date(request.GET.get("from", ""))
    date_to = _parse_date(request.GET.get("to", ""))

    qs = LedgerEntry.objects.select_related("user", "user__profile_v2")
    if reason in LedgerReason.values:
        qs = qs.filter(reason=reason)
    else:
        reason = ""
    if q:
        query = Q(user__username__icontains=q) | Q(user__profile_v2__steam_personaname__icontains=q)
        if q.isdigit():
            query |= Q(user_id=int(q))
        qs = qs.filter(query)
    if direction == "in":
        qs = qs.filter(delta__gt=0)
    elif direction == "out":
        qs = qs.filter(delta__lt=0)
    else:
        direction = ""
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    filters = {
        "reason": reason,
        "q": q,
        "direction": direction,
        "from": date_from.isoformat() if date_from else "",
        "to": date_to.isoformat() if date_to else "",
    }
    return qs.order_by("-created_at", "-id"), filters


@login_required
def ledger(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    qs, filters = _filtered_ledger(request)
    totals = qs.aggregate(
        count=Count("id"),
        income=Sum("delta", filter=Q(delta__gt=0)),
        spent=Sum("delta", filter=Q(delta__lt=0)),
        net=Sum("delta"),
    )
    reason_labels = dict(LedgerReason.choices)
    breakdown = [
        {**row, "label": reason_labels.get(row["reason"], row["reason"])}
        for row in qs.order_by().values("reason").annotate(total=Sum("delta"), count=Count("id")).order_by("-count")
    ]
    peak = max((row["count"] for row in breakdown), default=0)
    for row in breakdown:
        row["percent"] = round(row["count"] * 100 / peak) if peak else 0

    page_obj = Paginator(qs, LEDGER_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": filters,
        "totals": {key: (value if value is not None else Decimal("0")) for key, value in totals.items()},
        "breakdown": breakdown,
        "reason_options": LedgerReason.choices,
        "pagination_query": _querystring(request),
        "export_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/ledger_content.html", context=context, title="Финансы · Control Center", tab="ledger")


@login_required
def ledger_export(request):
    if _admin_access(request) is None:
        return _forbidden()
    qs, _filters = _filtered_ledger(request)
    response, writer = _csv_response(f"caseup-ledger-{timezone.localdate():%Y%m%d}.csv")
    writer.writerow(["id", "created_at", "user_id", "username", "reason", "delta", "balance_after", "idempotency_key"])
    for entry in qs[:CSV_EXPORT_LIMIT]:
        writer.writerow(
            [
                entry.id,
                timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                entry.user_id,
                entry.user.username,
                entry.reason,
                entry.delta,
                entry.balance_after,
                entry.idempotency_key or "",
            ]
        )
    return response


# --------------------------------------------------------------------------- payments


def _filtered_payments(request):
    from modules.v2.payments.models import Payment

    status = (request.GET.get("status") or "").strip()
    provider = (request.GET.get("provider") or "").strip()
    mode = (request.GET.get("mode") or "").strip()
    q = (request.GET.get("q") or "").strip()
    date_from = _parse_date(request.GET.get("from", ""))
    date_to = _parse_date(request.GET.get("to", ""))

    qs = Payment.objects.select_related("user", "user__profile_v2")
    if status in Payment.Status.values:
        qs = qs.filter(status=status)
    else:
        status = ""
    if provider:
        qs = qs.filter(provider=provider)
    if mode == "test":
        qs = qs.filter(is_test=True)
    elif mode == "live":
        qs = qs.filter(is_test=False)
    else:
        mode = ""
    if q:
        query = Q(user__username__icontains=q) | Q(external_id__icontains=q) | Q(user__profile_v2__steam_personaname__icontains=q)
        if q.isdigit():
            query |= Q(user_id=int(q))
        try:
            query |= Q(public_id=uuid.UUID(q))
        except ValueError:
            pass
        qs = qs.filter(query)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    filters = {
        "status": status,
        "provider": provider,
        "mode": mode,
        "q": q,
        "from": date_from.isoformat() if date_from else "",
        "to": date_to.isoformat() if date_to else "",
    }
    return qs.order_by("-created_at", "-id"), filters


def _payments_setup_summary() -> dict:
    from modules.v2.payments.ip_allowlist import webhook_ip_check_enabled
    from modules.v2.payments.providers import get_provider
    from modules.v2.payments.services import unpaid_payment_timeout

    provider = get_provider()
    base_url = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return {
        "provider": provider,
        "webhook_url": f"{base_url}{reverse('payments_public:webhook', args=[provider.code])}" if provider else "",
        "ip_check": webhook_ip_check_enabled(),
        "reconcile_minutes": int(getattr(settings, "CELERY_BEAT_PAYMENTS_RECONCILE_SECONDS", 300)) // 60,
        "unpaid_timeout_minutes": round(unpaid_payment_timeout().total_seconds() / 60),
    }


@login_required
def payments(request):
    from modules.v2.payments.models import Payment

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    qs, filters = _filtered_payments(request)
    totals = qs.aggregate(
        count=Count("id"),
        succeeded_sum=Sum("amount", filter=Q(status=Payment.Status.SUCCEEDED)),
        succeeded=Count("id", filter=Q(status=Payment.Status.SUCCEEDED)),
        open=Count("id", filter=Q(status__in=(Payment.Status.CREATED, Payment.Status.PENDING))),
        failed=Count("id", filter=Q(status__in=(Payment.Status.FAILED, Payment.Status.CANCELED))),
    )
    status_labels = dict(Payment.Status.choices)
    breakdown = [
        {**row, "label": status_labels.get(row["status"], row["status"])}
        for row in qs.order_by().values("status").annotate(total=Sum("amount"), count=Count("id")).order_by("-count")
    ]
    peak = max((row["count"] for row in breakdown), default=0)
    for row in breakdown:
        row["percent"] = round(row["count"] * 100 / peak) if peak else 0

    page_obj = Paginator(qs, PAYMENTS_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": filters,
        "totals": {key: (value if value is not None else Decimal("0")) for key, value in totals.items()},
        "breakdown": breakdown,
        "status_options": Payment.Status.choices,
        "provider_options": list(Payment.objects.order_by().values_list("provider", flat=True).distinct()),
        "setup": _payments_setup_summary(),
        "pagination_query": _querystring(request),
        "export_query": _querystring(request),
        "current_path": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/payments_content.html", context=context, title="Платежи · Control Center", tab="payments")


@login_required
def payments_export(request):
    if _admin_access(request) is None:
        return _forbidden()
    qs, _filters = _filtered_payments(request)
    response, writer = _csv_response(f"caseup-payments-{timezone.localdate():%Y%m%d}.csv")
    writer.writerow(["public_id", "created_at", "paid_at", "user_id", "username", "provider", "external_id", "amount", "currency", "status", "is_test", "error"])
    for payment in qs[:CSV_EXPORT_LIMIT]:
        writer.writerow(
            [
                payment.public_id,
                timezone.localtime(payment.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                timezone.localtime(payment.paid_at).strftime("%Y-%m-%d %H:%M:%S") if payment.paid_at else "",
                payment.user_id,
                payment.user.username,
                payment.provider,
                payment.external_id,
                payment.amount,
                payment.currency,
                payment.status,
                int(payment.is_test),
                payment.error_message,
            ]
        )
    return response


@login_required
@require_POST
def payment_recheck(request, payment_id: int):
    from modules.v2.payments.models import Payment
    from modules.v2.payments.providers import PaymentProviderError
    from modules.v2.payments.services import sync_payment

    if _admin_access(request) is None:
        return _forbidden()
    payment = get_object_or_404(Payment.objects.select_related("user"), pk=payment_id)
    previous = payment.status
    try:
        payment = sync_payment(payment)
    except PaymentProviderError as exc:
        messages.error(request, f"Провайдер не ответил: {exc}")
    else:
        if payment.status != previous:
            messages.success(request, f"Статус обновлён: {previous} → {payment.status}.")
        else:
            messages.info(request, f"Статус не изменился: {payment.get_status_display()}.")
    log_admin_action(
        actor=request.user,
        action=AuditAction.PAYMENT_RECHECK,
        target_user=payment.user,
        summary=f"{payment.public_id}: {previous} → {payment.status}",
        payload={"payment": str(payment.public_id), "from": previous, "to": payment.status},
        request=request,
    )
    return redirect(_safe_next(request, reverse("control_center:payments")))


# --------------------------------------------------------------------------- catalog moderation


@login_required
def moderation(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    from modules.v2.steam_catalog.models import SteamItem

    scope = (request.GET.get("scope") or "review").strip()
    item_type = (request.GET.get("type") or "").strip()
    q = (request.GET.get("q") or "").strip()
    qs = SteamItem.objects.all()
    if scope == "hidden":
        qs = qs.filter(is_active=False)
    elif scope == "no_price":
        qs = qs.filter(is_active=True).filter(Q(price_rub__isnull=True) | Q(price_rub__lte=0))
    else:
        scope = "review"
        qs = qs.filter(needs_review=True)
    if item_type in SteamItem.ItemType.values:
        qs = qs.filter(item_type=item_type)
    else:
        item_type = ""
    if q:
        qs = qs.filter(Q(market_hash_name__icontains=q) | Q(name__icontains=q) | Q(name_ru__icontains=q))

    page_obj = Paginator(qs.order_by("-created_at", "-id"), MODERATION_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": {"scope": scope, "type": item_type, "q": q},
        "type_options": SteamItem.ItemType.choices,
        "scope_counts": {
            "review": SteamItem.objects.filter(needs_review=True).count(),
            "hidden": SteamItem.objects.filter(is_active=False).count(),
            "no_price": SteamItem.objects.filter(is_active=True).filter(Q(price_rub__isnull=True) | Q(price_rub__lte=0)).count(),
        },
        "verifications_pending": VerificationRequest.objects.filter(status=VerificationRequest.Status.PENDING).count(),
        "pagination_query": _querystring(request),
        "current_url": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/moderation_content.html", context=context, title="Модерация · Control Center", tab="moderation")


@login_required
@require_POST
def moderation_action(request):
    if _admin_access(request) is None:
        return _forbidden()
    from modules.v2.steam_catalog.models import SteamItem

    action = (request.POST.get("action") or "").strip()
    ids = [int(raw) for raw in request.POST.getlist("items") if str(raw).isdigit()]
    fallback = reverse("control_center:moderation")
    if not ids:
        messages.error(request, "Не выбрано ни одного предмета.")
        return redirect(_safe_next(request, fallback))

    items = SteamItem.objects.filter(pk__in=ids)
    names = list(items.values_list("market_hash_name", flat=True)[:20])
    if action == "approve":
        updated = items.update(needs_review=False, is_active=True, updated_at=timezone.now())
        audit_action = AuditAction.CATALOG_APPROVE
        messages.success(request, f"Одобрено предметов: {updated}.")
    elif action == "disable":
        updated = items.update(needs_review=False, is_active=False, updated_at=timezone.now())
        audit_action = AuditAction.CATALOG_DISABLE
        messages.success(request, f"Скрыто предметов: {updated}.")
    else:
        messages.error(request, "Неизвестное действие.")
        return redirect(_safe_next(request, fallback))

    log_admin_action(
        actor=request.user,
        action=audit_action,
        summary=f"{updated}: {', '.join(names[:3])}{'…' if len(names) > 3 else ''}",
        payload={"item_ids": ids[:200], "names": names},
        request=request,
    )
    return redirect(_safe_next(request, fallback))


# --------------------------------------------------------------------------- health


def _push_summary():
    from modules.v2.webpush.categories import CATEGORIES
    from modules.v2.webpush.services import admin_summary

    try:
        summary = admin_summary()
    except Exception:  # noqa: BLE001 - the status page must open even if this block fails
        return None
    labels = {**CATEGORIES, "test": "Тестовые", "other": "Прочее"}
    summary["by_category"] = [{"label": labels.get(row["category"], row["category"]), "total": row["total"]} for row in summary["by_category"]]
    return summary


@login_required
def system_health(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    checks = health.full_checks()
    from modules.v2.system.services import get_external_service_integration, get_module_state_map
    from modules.v2.system.module_registry import MODULE_REGISTRY

    integration = get_external_service_integration()
    state_map = get_module_state_map()
    try:
        from modules.v2.celery_tasks.models import PriceAutoUpdateRunLog

        recent_runs = PriceAutoUpdateRunLog.objects.select_related("rule").order_by("-started_at")[:8]
    except Exception:  # noqa: BLE001
        recent_runs = []
    context = {
        "role_access": role_access,
        "checks": checks,
        "overall": health.overall_status(checks),
        "runtime": health.runtime_info(),
        "summary": get_control_center_summary(),
        "integrations": [
            ("Steam API", bool(integration.steam_api_key)),
            ("FACEIT API", bool(integration.faceit_api_key)),
            ("Telegram Bot", bool(integration.telegram_bot_token)),
        ],
        "modules": [
            {"key": key, "label": module.label, "enabled": bool(state_map.get(key, module.default_enabled))}
            for key, module in MODULE_REGISTRY.items()
        ],
        "recent_runs": recent_runs,
        "push": _push_summary(),
        "checked_at": timezone.now(),
    }
    return _render(request, partial="control_center/partials/health_content.html", context=context, title="Система · Control Center", tab="health")


# --------------------------------------------------------------------------- error journal

ERRORS_PAGE_SIZE = 30


def error_journal(request):
    from modules.v2.system.models import ErrorGroup

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    show = request.GET.get("show") or "open"
    qs = ErrorGroup.objects.all()
    if show == "resolved":
        qs = qs.filter(resolved_at__isnull=False)
    elif show != "all":
        show = "open"
        qs = qs.filter(resolved_at__isnull=True)
    day_ago = timezone.now() - timedelta(hours=24)
    open_qs = ErrorGroup.objects.filter(resolved_at__isnull=True)
    context = {
        "role_access": role_access,
        "page_obj": Paginator(qs.order_by("-last_seen"), ERRORS_PAGE_SIZE).get_page(request.GET.get("page") or 1),
        "show": show,
        "stats": {
            "open": open_qs.count(),
            "day": open_qs.filter(last_seen__gte=day_ago).count(),
            "hits_day": open_qs.filter(last_seen__gte=day_ago).aggregate(total=Sum("count"))["total"] or 0,
        },
        "pagination_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/errors_content.html", context=context, title="Ошибки · Control Center", tab="errors")


@require_POST
def error_journal_action(request):
    from modules.v2.system.alerts import send_admin_alert
    from modules.v2.system.models import ErrorGroup

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    action = request.POST.get("action")
    if action == "resolve":
        updated = ErrorGroup.objects.filter(pk=request.POST.get("group_id"), resolved_at__isnull=True).update(resolved_at=timezone.now())
        if updated:
            messages.success(request, "Ошибка отмечена решённой. Если повторится — откроется снова и придёт тревога.")
    elif action == "resolve_all":
        updated = ErrorGroup.objects.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())
        messages.success(request, f"Отмечено решёнными: {updated}.")
    elif action == "test_alert":
        result = send_admin_alert(f"test:{timezone.now().timestamp()}", "Проверка тревог", f"Тестовая тревога от {request.user.username}. Если вы это видите — канал работает.", url="/control-center/errors/")
        channels = [name for name, ok in (("Telegram", result["telegram"]), ("почта", result["mail"])) if ok]
        channels.append(f"уведомления админам: {result['notified']}")
        messages.success(request, "Тревога отправлена: " + ", ".join(channels) + ".")
    return redirect(f"{reverse('control_center:errors')}?{request.POST.get('return_query', '')}")


# --------------------------------------------------------------------------- activity log


def _filtered_activity(request):
    from .models import ActivityCategory, ActivityLog

    category = (request.GET.get("category") or "").strip()
    event = (request.GET.get("event") or "").strip()[:64]
    q = (request.GET.get("q") or "").strip()
    user_raw = (request.GET.get("user") or "").strip()
    include_requests = request.GET.get("requests") == "1"
    date_from = _parse_date(request.GET.get("from", ""))
    date_to = _parse_date(request.GET.get("to", ""))

    qs = ActivityLog.objects.select_related("user", "user__profile_v2", "actor")
    if category in ActivityCategory.values:
        qs = qs.filter(category=category)
    else:
        category = ""
        if not include_requests and not event.startswith("request"):
            # Raw HTTP requests duplicate the domain events; they are opt-in to keep the feed readable.
            qs = qs.exclude(category=ActivityCategory.REQUEST)
    if event:
        qs = qs.filter(event=event)
    target_user = None
    if user_raw.isdigit():
        target_user = User.objects.filter(pk=int(user_raw)).first()
        qs = qs.filter(Q(user_id=int(user_raw)) | Q(actor_id=int(user_raw)))
    else:
        user_raw = ""
    if q:
        query = (
            Q(username__icontains=q)
            | Q(actor_username__icontains=q)
            | Q(summary__icontains=q)
            | Q(object_id=q)
            | Q(path__icontains=q)
        )
        try:
            query |= Q(ip_address=str(ipaddress.ip_address(q)))
        except ValueError:
            pass
        qs = qs.filter(query)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    filters = {
        "category": category,
        "event": event,
        "q": q,
        "user": user_raw,
        "requests": include_requests,
        "from": date_from.isoformat() if date_from else "",
        "to": date_to.isoformat() if date_to else "",
    }
    return qs.order_by("-created_at", "-id"), filters, target_user


@login_required
def activity_log(request):
    from .models import ActivityCategory, ActivityLog
    from .services.activity import EVENT_LABELS, event_label

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    qs, filters, target_user = _filtered_activity(request)

    day_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    today = ActivityLog.objects.filter(created_at__gte=day_start).exclude(category=ActivityCategory.REQUEST)
    today_stats = today.aggregate(
        events=Count("id"),
        users=Count("user", distinct=True),
        topups=Sum("amount", filter=Q(event="balance.topup")),
        failed_logins=Count("id", filter=Q(event="auth.login_failed")),
    )

    category_labels = dict(ActivityCategory.choices)
    breakdown = [
        {**row, "label": category_labels.get(row["category"], row["category"])}
        for row in qs.order_by().values("category").annotate(count=Count("id")).order_by("-count")
    ]
    peak = max((row["count"] for row in breakdown), default=0)
    for row in breakdown:
        row["percent"] = round(row["count"] * 100 / peak) if peak else 0

    page_obj = Paginator(qs, ACTIVITY_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    for entry in page_obj:
        entry.event_label = event_label(entry.event)
        entry.payload_text = json.dumps(entry.payload, ensure_ascii=False, indent=2, sort_keys=True) if entry.payload else ""

    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": filters,
        "target_user": target_user,
        "today": {key: (value if value is not None else 0) for key, value in today_stats.items()},
        "breakdown": breakdown,
        "category_options": ActivityCategory.choices,
        "event_options": sorted(EVENT_LABELS.items(), key=lambda item: item[0]),
        "retention": {
            "events": int(getattr(settings, "ACTIVITY_LOG_RETENTION_DAYS", 365)),
            "requests": int(getattr(settings, "ACTIVITY_LOG_REQUEST_RETENTION_DAYS", 90)),
        },
        "pagination_query": _querystring(request),
        "export_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/activity_content.html", context=context, title="Логи · Control Center", tab="activity")


@login_required
def activity_log_export(request):
    from .services.activity import event_label

    if _admin_access(request) is None:
        return _forbidden()
    qs, _filters, _target = _filtered_activity(request)
    response, writer = _csv_response(f"caseup-activity-{timezone.localdate():%Y%m%d}.csv")
    writer.writerow(["id", "created_at", "category", "event", "event_label", "summary", "user_id", "username", "actor_id", "actor_username", "amount", "object_type", "object_id", "ip", "user_agent", "method", "path", "status", "payload"])
    for entry in qs[:CSV_EXPORT_LIMIT]:
        writer.writerow(
            [
                entry.pk,
                timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                entry.category,
                entry.event,
                event_label(entry.event),
                entry.summary,
                entry.user_id or "",
                entry.username,
                entry.actor_id or "",
                entry.actor_username,
                entry.amount if entry.amount is not None else "",
                entry.object_type,
                entry.object_id,
                entry.ip_address or "",
                entry.user_agent,
                entry.method,
                entry.path,
                entry.status_code or "",
                json.dumps(entry.payload, ensure_ascii=False, sort_keys=True) if entry.payload else "",
            ]
        )
    return response


# --------------------------------------------------------------------------- referrals

REFERRALS_PAGE_SIZE = 40
REFERRAL_STATUS_FILTERS = (
    ("flagged", "На проверке"),
    ("counted", "Засчитаны"),
    ("waiting", "Ждут пополнения"),
    ("blocked", "Отклонены"),
    ("", "Все"),
)


@login_required
def referrals(request):
    from modules.v2.referrals.models import Referral, ReferralBonus
    from modules.v2.referrals.services import discount_percent_for, turnover_percent_for

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    qs = Referral.objects.select_related("referrer", "referrer__profile_v2", "referee", "referee__profile_v2", "reviewed_by")
    if status == "flagged":
        qs = qs.filter(review=Referral.Review.FLAGGED)
    elif status == "counted":
        qs = qs.filter(review=Referral.Review.OK, qualified_at__isnull=False)
    elif status == "waiting":
        qs = qs.filter(qualified_at__isnull=True).exclude(review=Referral.Review.BLOCKED)
    elif status == "blocked":
        qs = qs.filter(review=Referral.Review.BLOCKED)
    else:
        status = ""
    if q:
        filters = Q(referrer__username__icontains=q) | Q(referee__username__icontains=q) | Q(referrer__profile_v2__steam_personaname__icontains=q) | Q(referee__profile_v2__steam_personaname__icontains=q) | Q(code__iexact=q)
        if q.isdigit():
            filters |= Q(referrer_id=int(q)) | Q(referee_id=int(q))
        qs = qs.filter(filters)
    page_obj = Paginator(qs.order_by("-created_at", "-id"), REFERRALS_PAGE_SIZE).get_page(request.GET.get("page") or 1)

    all_referrals = Referral.objects.all()
    totals = all_referrals.aggregate(
        total=Count("id"),
        counted=Count("id", filter=Q(review=Referral.Review.OK, qualified_at__isnull=False)),
        flagged=Count("id", filter=Q(review=Referral.Review.FLAGGED)),
    )
    totals["bonuses"] = ReferralBonus.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    top = list(
        User.objects.filter(referrals_made__review=Referral.Review.OK, referrals_made__qualified_at__isnull=False)
        .annotate(counted=Count("referrals_made"))
        .select_related("profile_v2")
        .order_by("-counted", "id")[:10]
    )
    for user in top:
        user.ref_discount = discount_percent_for(user.counted)
        user.ref_turnover = turnover_percent_for(user.counted)

    from modules.v2.referrals.models import ReferralProgramSettings, ReferralWelcomeBonus
    from modules.v2.referrals.welcome import admin_totals
    from modules.v2.subscriptions.models import SubscriptionPlan

    welcome = {}
    for row in ReferralWelcomeBonus.objects.filter(referral_id__in=[item.pk for item in page_obj]):
        welcome.setdefault(row.referral_id, {})[row.role] = row
    for item in page_obj:
        item.welcome = welcome.get(item.pk, {})

    context = {
        "role_access": role_access,
        "welcome_config": ReferralProgramSettings.load(),
        "welcome_totals": admin_totals(),
        "welcome_plans": SubscriptionPlan.objects.filter(is_active=True, family=SubscriptionPlan.Family.SITE).order_by("level"),
        "page_obj": page_obj,
        "filters": {"status": status, "q": q},
        "status_options": REFERRAL_STATUS_FILTERS,
        "totals": totals,
        "top_referrers": top,
        "pagination_query": _querystring(request),
        "current_path": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/referrals_content.html", context=context, title="Рефералы · Control Center", tab="referrals")


@login_required
@require_POST
def referral_review(request, referral_id: int):
    from modules.v2.referrals.models import Referral
    from modules.v2.referrals.services import ReferralReviewError, review_referral

    if _admin_access(request) is None:
        return _forbidden()
    referral = get_object_or_404(Referral.objects.select_related("referrer", "referee"), pk=referral_id)
    decision = (request.POST.get("decision") or "").strip()
    try:
        review_referral(actor=request.user, referral=referral, decision=decision, note=request.POST.get("note", ""), request=request)
        messages.success(request, f"Реферал {referral.referee.username} {'засчитан' if decision == Referral.Review.OK else 'отклонён'}.")
    except ReferralReviewError as exc:
        messages.error(request, str(exc))
    return redirect(_safe_next(request, reverse("control_center:referrals")))


@login_required
@require_POST
def referral_settings(request):
    from modules.v2.referrals.welcome import SettingsError, save_settings

    if _admin_access(request) is None:
        return _forbidden()
    try:
        changes = save_settings(form=request.POST)
    except SettingsError as exc:
        messages.error(request, str(exc))
    else:
        if changes:
            log_admin_action(actor=request.user, action=AuditAction.SETTINGS_UPDATE, summary=("Рефералы: " + "; ".join(changes))[:255], payload={"section": "referrals", "changes": changes}, request=request)
            messages.success(request, "Настройки реферального бонуса сохранены.")
        else:
            messages.info(request, "Изменений нет.")
    return redirect("control_center:referrals")


# --------------------------------------------------------------------------- promo codes

PROMO_PAGE_SIZE = 40


@login_required
def promo_codes(request):
    from modules.v2.promo.models import PromoActivation, PromoCode

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    state = (request.GET.get("state") or "").strip()
    q = (request.GET.get("q") or "").strip()
    qs = PromoCode.objects.select_related("owner", "owner__profile_v2", "case")
    now = timezone.now()
    if state == "active":
        qs = qs.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    elif state == "off":
        qs = qs.filter(Q(is_active=False) | Q(expires_at__lte=now))
    else:
        state = ""
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(title__icontains=q) | Q(owner__username__icontains=q))
    page_obj = Paginator(qs.order_by("-created_at", "-id"), PROMO_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    totals = {
        "codes": PromoCode.objects.count(),
        "active": PromoCode.objects.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count(),
        "activations": PromoActivation.objects.count(),
        "paid": PromoActivation.objects.filter(ledger_entry__isnull=False).aggregate(total=Sum("benefit_amount"))["total"] or Decimal("0"),
    }
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": {"state": state, "q": q},
        "totals": totals,
        "pagination_query": _querystring(request),
        "current_path": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/promo_list_content.html", context=context, title="Промокоды · Control Center", tab="promo")


@login_required
def promo_code_edit(request, promo_id: int | None = None):
    from modules.v2.promo.forms import PromoCodeForm
    from modules.v2.promo.models import PromoCode

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    promo = get_object_or_404(PromoCode, pk=promo_id) if promo_id else None
    form = PromoCodeForm(request.POST or None, instance=promo)
    if request.method == "POST" and form.is_valid():
        is_new = promo is None
        saved = form.save(commit=False)
        if is_new:
            saved.created_by = request.user
        saved.save()
        log_admin_action(
            actor=request.user,
            action=AuditAction.PROMO_CREATE if is_new else AuditAction.PROMO_UPDATE,
            target_user=saved.owner,
            summary=f"{saved.code}: {saved.benefit_label}" + ("" if saved.is_active else " (выключен)"),
            payload={"promo_id": saved.pk, "changed": sorted(form.changed_data)},
            request=request,
        )
        messages.success(request, f"Промокод {saved.code} {'создан' if is_new else 'сохранён'}.")
        return redirect("control_center:promo_edit", promo_id=saved.pk)

    activations = []
    if promo is not None:
        activations = list(promo.activations.select_related("user", "user__profile_v2").order_by("-created_at", "-id")[:200])
    context = {
        "role_access": role_access,
        "form": form,
        "promo": promo,
        "activations": activations,
        "share_url": request.build_absolute_uri(reverse("promo:dashboard") + f"?code={promo.code}") if promo else "",
        "current_path": request.get_full_path(),
    }
    title = f"{promo.code} · Промокоды" if promo else "Новый промокод"
    return _render(request, partial="control_center/partials/promo_edit_content.html", context=context, title=title, tab="promo")


@login_required
@require_POST
def promo_activation_cancel(request, activation_id: int):
    from modules.v2.promo.models import PromoActivation
    from modules.v2.promo.services import PromoError, cancel_activation

    if _admin_access(request) is None:
        return _forbidden()
    activation = get_object_or_404(PromoActivation.objects.select_related("promo", "user"), pk=activation_id)
    try:
        cancel_activation(actor=request.user, activation=activation, request=request)
        messages.success(request, f"Активация {activation.promo.code} у {activation.user.username} отменена.")
    except PromoError as exc:
        messages.error(request, str(exc))
    return redirect(_safe_next(request, reverse("control_center:promo_edit", args=[activation.promo_id])))


# --------------------------------------------------------------------------- multi-accounts & IP bans

MULTI_ACCOUNT_PAGE_SIZE = 30


@login_required
def multi_accounts(request):
    from django.core.cache import cache as django_cache

    from modules.v2.antifraud.models import IpBan, MultiAccountCase

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    status = (request.GET.get("status") or "open").strip()
    qs = MultiAccountCase.objects.annotate(members_count=Count("memberships"))
    if status in MultiAccountCase.Status.values:
        qs = qs.filter(status=status)
    else:
        status = "all"
    page_obj = Paginator(qs.order_by("-updated_at", "-id"), MULTI_ACCOUNT_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    cases = list(page_obj)
    preview = {}
    if cases:
        from modules.v2.antifraud.models import MultiAccountMember

        for membership in MultiAccountMember.objects.filter(case__in=cases).select_related("user", "user__profile_v2").order_by("added_at", "id"):
            preview.setdefault(membership.case_id, []).append(membership)
    for case in cases:
        case.preview_members = preview.get(case.pk, [])[:6]
        case.has_new = any(member.is_new for member in preview.get(case.pk, []))
    counts = dict(MultiAccountCase.objects.values("status").annotate(n=Count("id")).values_list("status", "n"))
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "cases": cases,
        "status": status,
        "status_tabs": [("open", "На проверке", counts.get("open", 0)), ("clean", "Чисто", counts.get("clean", 0)), ("abuse", "Абуз", counts.get("abuse", 0)), ("all", "Все", sum(counts.values()))],
        "counts": counts,
        "ip_bans": list(IpBan.objects.active().select_related("created_by", "case")[:50]),
        "shared_networks": django_cache.get("antifraud:shared-networks") or [],
        "duration_choices": BAN_DURATION_CHOICES,
        "pagination_query": _querystring(request),
        "current_path": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/multi_accounts_content.html", context=context, title="Мульти-аккаунты · Control Center", tab="multi_accounts")


@login_required
def multi_account_detail(request, case_id: int):
    from modules.v2.accounts.geoip import lookup
    from modules.v2.antifraud.models import IpBan, MultiAccountCase, UserIp
    from modules.v2.payments.models import Payment
    from modules.v2.profiles.rbac import can_manage_user
    from modules.v2.referrals.models import Referral

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    case = get_object_or_404(MultiAccountCase.objects.select_related("decided_by"), pk=case_id)
    memberships = list(case.memberships.select_related("user", "user__profile_v2").order_by("added_at", "id"))
    user_ids = [membership.user_id for membership in memberships]
    topups = dict(
        Payment.objects.filter(user_id__in=user_ids, status=Payment.Status.SUCCEEDED, is_test=False)
        .values("user_id").annotate(total=Sum("amount")).values_list("user_id", "total")
    )
    banned = set(UserBan.objects.active().filter(user_id__in=user_ids).values_list("user_id", flat=True))
    ips_by_user = {}
    for row in UserIp.objects.filter(user_id__in=user_ids, ip__in=case.shared_ips or []).order_by("-last_seen"):
        ips_by_user.setdefault(row.user_id, []).append(row)
    for membership in memberships:
        membership.topup_total = topups.get(membership.user_id) or Decimal("0")
        membership.is_banned = membership.user_id in banned
        membership.can_manage = can_manage_user(request.user, membership.user)
        membership.ips = ips_by_user.get(membership.user_id, [])
    shared = []
    for ip in case.shared_ips or []:
        city, country = lookup(ip)
        shared.append({
            "ip": ip,
            "location": ", ".join(part for part in (city, country) if part),
            "accounts": UserIp.objects.filter(ip=ip).count(),
            "banned": IpBan.objects.active().filter(network__in=[ip, f"{ip}/32", f"{ip}/128"]).exists(),
        })
    referrals = list(Referral.objects.filter(referrer_id__in=user_ids, referee_id__in=user_ids).select_related("referrer", "referee"))
    context = {
        "role_access": role_access,
        "case": case,
        "memberships": memberships,
        "shared": shared,
        "internal_referrals": referrals,
        "duration_choices": BAN_DURATION_CHOICES,
        "current_path": request.get_full_path(),
    }
    return _render(request, partial="control_center/partials/multi_account_detail_content.html", context=context, title=f"Группа #{case.pk} · Мульти-аккаунты", tab="multi_accounts")


@login_required
@require_POST
def multi_account_decide(request, case_id: int):
    from modules.v2.antifraud.models import MultiAccountCase
    from modules.v2.antifraud.services import DecisionError, mark_abuse, mark_clean

    if _admin_access(request) is None:
        return _forbidden()
    case = get_object_or_404(MultiAccountCase, pk=case_id)
    decision = request.POST.get("decision", "")
    note = (request.POST.get("note") or "").strip()[:500]
    try:
        if decision == "clean":
            mark_clean(actor=request.user, case=case, note=note, request=request)
            messages.success(request, f"Группа #{case.pk} отмечена как чистая. Если в ней появится новый аккаунт — придёт уведомление.")
        elif decision == "abuse":
            result = mark_abuse(
                actor=request.user, case=case, user_ids=request.POST.getlist("ban_user"), duration=request.POST.get("duration", "7d"),
                ban_ips=request.POST.get("ban_ips") == "1", ip_duration=request.POST.get("ip_duration", "7d"), note=note, request=request,
            )
            text = f"Группа #{case.pk}: забанено аккаунтов — {len(result['banned'])}, IP — {len(result['ip_bans'])}."
            if result["skipped"]:
                text += " Пропущены (нет прав): " + ", ".join(result["skipped"])
            messages.success(request, text)
        else:
            messages.error(request, "Неизвестное решение.")
    except (DecisionError, UserManagementError) as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("control_center:multi_account_detail", case_id=case.pk)


@login_required
@require_POST
def ip_ban_create(request):
    from modules.v2.antifraud.services import DecisionError, create_ip_ban

    if _admin_access(request) is None:
        return _forbidden()
    try:
        ban = create_ip_ban(actor=request.user, network=request.POST.get("network", ""), reason=request.POST.get("reason", ""), duration=request.POST.get("duration", "7d"), request=request)
        messages.success(request, f"IP {ban.network} заблокирован.")
    except DecisionError as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(_safe_next(request, reverse("control_center:multi_accounts")))


@login_required
@require_POST
def ip_ban_lift(request, ban_id: int):
    from modules.v2.antifraud.models import IpBan
    from modules.v2.antifraud.services import lift_ip_ban

    if _admin_access(request) is None:
        return _forbidden()
    ban = get_object_or_404(IpBan, pk=ban_id, lifted_at__isnull=True)
    lift_ip_ban(actor=request.user, ban=ban, request=request)
    messages.success(request, f"Бан IP {ban.network} снят.")
    return redirect(_safe_next(request, reverse("control_center:multi_accounts")))


# --------------------------------------------------------------------------- business

BUSINESS_JOURNAL_PAGE_SIZE = 50


def _chief_access(request):
    access = get_user_role(request.user)
    return access if access.is_chief_plus else None


def _business_period(request):
    from modules.v2.business.stats import resolve_period

    key = (request.GET.get("period") or "").strip()
    date_from = _parse_date(request.GET.get("from", "")) if key == "custom" else None
    date_to = _parse_date(request.GET.get("to", "")) if key == "custom" else None
    return resolve_period(key, date_from, date_to)


def _attach_users(rows: list[dict]) -> list[dict]:
    users = User.objects.select_related("profile_v2").in_bulk([row["user_id"] for row in rows])
    return [{**row, "user": users.get(row["user_id"])} for row in rows]


@login_required
def business(request):
    from modules.v2.business.models import RevenueCategory
    from modules.v2.business.stats import CATEGORY_ORDER, PERIOD_PRESETS, build_dashboard

    role_access = _chief_access(request)
    if role_access is None:
        return HttpResponseForbidden("Бизнес-панель доступна только руководителю и его заместителю.")
    period = _business_period(request)
    data = build_dashboard(period)
    labels = dict(RevenueCategory.choices)
    money, changes = data["money"], data["changes"]
    categories = [
        {"code": code, "label": labels[code], "total": money["categories"][code], "change": changes.get("categories", {}).get(code)}
        for code in CATEGORY_ORDER
    ]
    context = {
        "role_access": role_access,
        "period": period,
        "period_presets": PERIOD_PRESETS,
        "period_query": _querystring(request),
        "data": data,
        "money": money,
        "changes": changes,
        "categories": categories,
        "top_topups": _attach_users(data["top_users"]["topups"]),
        "top_revenue": _attach_users(data["top_users"]["revenue"]),
    }
    return _render(request, partial="control_center/partials/business_content.html", context=context, title="Бизнес · Control Center", tab="business")


def _business_journal_filters(request):
    from modules.v2.business.stats import revenue_journal

    period = _business_period(request)
    category = (request.GET.get("category") or "").strip()
    source = (request.GET.get("source") or "").strip()
    return period, category, source, revenue_journal(period, category=category, source=source)


@login_required
def business_revenue(request):
    from modules.v2.business.models import RevenueCategory, RevenueSource
    from modules.v2.business.stats import PERIOD_PRESETS

    role_access = _chief_access(request)
    if role_access is None:
        return HttpResponseForbidden("Бизнес-панель доступна только руководителю и его заместителю.")
    period, category, source, qs = _business_journal_filters(request)
    totals = qs.aggregate(total=Sum("amount"), count=Count("id"))
    page_obj = Paginator(qs, BUSINESS_JOURNAL_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    context = {
        "role_access": role_access,
        "period": period,
        "period_presets": PERIOD_PRESETS,
        "filters": {"category": category, "source": source},
        "category_options": RevenueCategory.choices,
        "source_options": RevenueSource.choices,
        "totals": {"total": totals["total"] or Decimal("0"), "count": totals["count"] or 0},
        "page_obj": page_obj,
        "pagination_query": _querystring(request),
        "export_query": _querystring(request),
    }
    return _render(request, partial="control_center/partials/business_revenue_content.html", context=context, title="Журнал доходов · Control Center", tab="business")


@login_required
def business_revenue_export(request):
    if _chief_access(request) is None:
        return HttpResponseForbidden("Бизнес-панель доступна только руководителю и его заместителю.")
    _period, _category, _source, qs = _business_journal_filters(request)
    response, writer = _csv_response(f"caseup-revenue-{timezone.localdate():%Y%m%d}.csv")
    writer.writerow(["id", "occurred_at", "category", "source", "amount", "user_id", "username", "ref", "note"])
    for entry in qs[:CSV_EXPORT_LIMIT]:
        writer.writerow(
            [
                entry.id,
                timezone.localtime(entry.occurred_at).strftime("%Y-%m-%d %H:%M:%S"),
                entry.get_category_display(),
                entry.get_source_display(),
                entry.amount,
                entry.user_id or "",
                entry.user.username if entry.user else "",
                entry.ref,
                entry.note,
            ]
        )
    return response


# --------------------------------------------------------------------------- daily bonus


@login_required
def daily_bonus(request):
    from modules.v2.case_simulator.models import SimCase
    from modules.v2.daily_bonus.models import CYCLE_DAYS, DailyBonusReward, DailyBonusSettings
    from modules.v2.daily_bonus.services import DailyBonusError, admin_summary, save_config

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    if request.method == "POST":
        try:
            changes = save_config(form=request.POST)
        except DailyBonusError as exc:
            messages.error(request, str(exc))
        else:
            if changes:
                log_admin_action(actor=request.user, action=AuditAction.DAILY_BONUS_UPDATE, summary="; ".join(changes)[:255], payload={"changes": changes}, request=request)
                messages.success(request, "Настройки бонуса дня сохранены.")
            else:
                messages.info(request, "Изменений нет.")
        return redirect("control_center:daily_bonus")

    rewards = {reward.day: reward for reward in DailyBonusReward.objects.select_related("case")}
    summary = admin_summary()
    rows = [{"day": day, "reward": rewards.get(day), "claims": summary["by_day"].get(day, 0)} for day in range(1, CYCLE_DAYS + 1)]
    context = {
        "role_access": role_access,
        "config": DailyBonusSettings.load(),
        "rows": rows,
        "kind_options": DailyBonusReward.Kind.choices,
        "cases": SimCase.objects.filter(is_active=True).exclude(kind=SimCase.Kind.TERMINAL).order_by("case_price", "name"),
        "summary": summary,
    }
    return _render(request, partial="control_center/partials/daily_bonus_content.html", context=context, title="Бонус дня · Control Center", tab="daily_bonus")


# --------------------------------------------------------------------------- donations


@login_required
def donations(request):
    from modules.v2.donations.forms import DonationGoalForm
    from modules.v2.donations.models import DonationGoal
    from modules.v2.donations import sponsors
    from modules.v2.donations.models import Sponsorship
    from modules.v2.donations.services import admin_summary, start_new_campaign

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    goal = DonationGoal.load()
    form = DonationGoalForm(instance=goal)
    if request.method == "POST":
        if request.POST.get("action") == "sponsor":
            sponsorship = get_object_or_404(Sponsorship.objects.select_related("user"), pk=int(request.POST.get("sponsorship_id") or 0) if str(request.POST.get("sponsorship_id") or "").isdigit() else 0)
            decision = request.POST.get("decision") or ""
            try:
                sponsors.decide(sponsorship, action=decision, actor=request.user)
            except sponsors.SponsorError as exc:
                messages.error(request, str(exc))
            else:
                verb = {"grant": "выдана", "decline": "отклонена", "revoke": "снята"}.get(decision, decision)
                log_admin_action(actor=request.user, action=AuditAction.DONATION_UPDATE, target_user=sponsorship.user, summary=f"Роль «Спонсор» {verb} ({sponsorship.get_kind_display().lower()})", payload={"sponsorship": sponsorship.pk, "decision": decision}, request=request)
                messages.success(request, f"Роль «Спонсор» {verb}: {sponsorship.user.username}.")
            return redirect(reverse("control_center:donations") + "#sponsors")
        if request.POST.get("action") == "new_campaign":
            start_new_campaign(goal)
            log_admin_action(actor=request.user, action=AuditAction.DONATION_UPDATE, summary="Начат новый сбор", payload={"started_at": goal.started_at.isoformat()}, request=request)
            messages.success(request, "Начат новый сбор: прогресс обнулён, история донатов сохранена.")
            return redirect("control_center:donations")
        form = DonationGoalForm(request.POST, instance=goal)
        if form.is_valid():
            changed = list(form.changed_data)
            if changed:
                form.save()
                labels = [str(form.fields[name].label) for name in changed]
                log_admin_action(actor=request.user, action=AuditAction.DONATION_UPDATE, summary=("Сбор: " + ", ".join(labels))[:255], payload={"changed": changed}, request=request)
                messages.success(request, "Настройки сбора сохранены.")
            else:
                messages.info(request, "Изменений нет.")
            return redirect("control_center:donations")
        messages.error(request, "Проверьте поля формы.")

    context = {
        "role_access": role_access,
        "goal": goal,
        "form": form,
        "summary": admin_summary(goal),
        "sponsor_candidates": Sponsorship.objects.filter(status=Sponsorship.Status.CANDIDATE).select_related("user", "user__profile_v2").order_by("created_at"),
        "sponsor_active": sponsors.active_sponsorships().select_related("user", "user__profile_v2", "decided_by"),
        "sponsor_month": sponsors.month_label(sponsors.month_start(timezone.localdate())),
        "sponsor_top": sponsors.monthly_top(sponsors.month_start(timezone.localdate()), goal),
        "sponsor_history": sponsors.history(),
    }
    return _render(request, partial="control_center/partials/donations_content.html", context=context, title="Донаты · Control Center", tab="donations")


# --------------------------------------------------------------------------- ads


@login_required
def ads(request):
    from modules.v2.ads.forms import AdSettingsForm
    from modules.v2.ads.models import AdPlacement, HouseBanner
    from modules.v2.ads.services import get_ad_settings

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    ad_settings = get_ad_settings()
    form = AdSettingsForm(instance=ad_settings)
    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "settings":
            form = AdSettingsForm(request.POST, instance=ad_settings)
            if form.is_valid():
                changed = list(form.changed_data)
                if changed:
                    form.save()
                    labels = [str(form.fields[name].label) for name in changed]
                    log_admin_action(actor=request.user, action=AuditAction.ADS_UPDATE, summary=("Реклама: " + ", ".join(labels))[:255], payload={"changed": changed}, request=request)
                    messages.success(request, "Настройки рекламы сохранены.")
                else:
                    messages.info(request, "Изменений нет.")
                return redirect("control_center:ads")
            messages.error(request, "Проверьте поля формы.")
        elif action == "placements":
            enabled = set(request.POST.getlist("placement_enabled"))
            switched = []
            for placement in AdPlacement.objects.all():
                is_enabled = str(placement.pk) in enabled
                if placement.is_enabled != is_enabled:
                    placement.is_enabled = is_enabled
                    placement.save(update_fields=["is_enabled", "updated_at"])
                    switched.append(f"{placement.name} — {'вкл' if is_enabled else 'выкл'}")
            if switched:
                log_admin_action(actor=request.user, action=AuditAction.ADS_UPDATE, summary=("Места: " + "; ".join(switched))[:255], payload={"enabled": sorted(enabled)}, request=request)
                messages.success(request, "Рекламные места сохранены.")
            else:
                messages.info(request, "Изменений нет.")
            return redirect(reverse("control_center:ads") + "#placements")
        elif action in ("banner_toggle", "banner_delete", "banner_reset"):
            raw_id = str(request.POST.get("banner_id") or "")
            banner = get_object_or_404(HouseBanner, pk=int(raw_id) if raw_id.isdigit() else 0)
            if action == "banner_toggle":
                banner.is_enabled = not banner.is_enabled
                banner.save(update_fields=["is_enabled", "updated_at"])
                summary = f"Баннер «{banner.name}» {'включён' if banner.is_enabled else 'выключен'}"
            elif action == "banner_reset":
                HouseBanner.objects.filter(pk=banner.pk).update(impressions=0, clicks=0)
                summary = f"Баннер «{banner.name}»: статистика обнулена"
            else:
                summary = f"Баннер «{banner.name}» удалён"
                banner.delete()
            log_admin_action(actor=request.user, action=AuditAction.ADS_UPDATE, summary=summary[:255], payload={"banner": raw_id, "action": action}, request=request)
            messages.success(request, summary + ".")
            return redirect(reverse("control_center:ads") + "#banners")

    banners = list(HouseBanner.objects.prefetch_related("placements"))
    totals = {
        "active": sum(1 for banner in banners if banner.is_enabled),
        "impressions": sum(banner.impressions for banner in banners),
        "clicks": sum(banner.clicks for banner in banners),
    }
    totals["ctr"] = round(totals["clicks"] * 100 / totals["impressions"], 2) if totals["impressions"] else 0
    context = {
        "role_access": role_access,
        "ad_settings": ad_settings,
        "form": form,
        "placements": AdPlacement.objects.annotate(banners_count=Count("house_banners")).order_by("sort_order", "id"),
        "banners": banners,
        "totals": totals,
        "now": timezone.now(),
    }
    return _render(request, partial="control_center/partials/ads_content.html", context=context, title="Реклама · Control Center", tab="ads")


@login_required
def ads_banner_edit(request, banner_id: int | None = None):
    from modules.v2.ads.forms import HouseBannerForm
    from modules.v2.ads.models import HouseBanner

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    banner = get_object_or_404(HouseBanner, pk=banner_id) if banner_id else None
    form = HouseBannerForm(request.POST or None, request.FILES or None, instance=banner)
    if request.method == "POST" and form.is_valid():
        is_new = banner is None
        saved = form.save()
        log_admin_action(
            actor=request.user,
            action=AuditAction.ADS_UPDATE,
            summary=(f"Баннер «{saved.name}» {'создан' if is_new else 'изменён'}")[:255],
            payload={"banner": saved.pk, "changed": sorted(form.changed_data)},
            request=request,
        )
        messages.success(request, f"Баннер «{saved.name}» {'создан' if is_new else 'сохранён'}.")
        return redirect("control_center:ads_banner_edit", banner_id=saved.pk)
    context = {"role_access": role_access, "form": form, "banner": banner}
    title = f"{banner.name} · Реклама" if banner else "Новый баннер"
    return _render(request, partial="control_center/partials/ads_banner_edit_content.html", context=context, title=title, tab="ads")


# --------------------------------------------------------------------------- case simulator


def _case_economy(case, items) -> dict:
    """Drop chances per item and the expected value of one opening (what a player gets back on average)."""
    from modules.v2.case_simulator.services import RARITY_ORDER, item_weights

    weights = item_weights(items)
    order = {rarity: index for index, rarity in enumerate(RARITY_ORDER)}
    rows = sorted(
        ({"item": item, "chance": (weight * 100).quantize(Decimal("0.001"))} for item, weight in weights),
        key=lambda row: (order.get(row["item"].rarity, 99), -row["item"].value, row["item"].name),
    )
    expected = sum((weight * (item.value or 0) for item, weight in weights), Decimal("0")).quantize(Decimal("0.01"))
    price = case.open_price
    return {
        "rows": rows,
        "expected": expected,
        "return_percent": (expected * 100 / price).quantize(Decimal("0.1")) if price else None,
        "has_custom_weights": any(item.weight is not None for item in items),
    }


@login_required
def sim_cases(request):
    from modules.v2.case_simulator.models import SimCase, SimCaseItem, SimOpening
    from modules.v2.daily_bonus.models import DailyBonusReward, DailyFreeCase
    from modules.v2.promo.models import PromoCode

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    if request.method == "POST":
        action = request.POST.get("action") or ""
        raw_id = str(request.POST.get("case_id") or "")
        case = get_object_or_404(SimCase, pk=int(raw_id) if raw_id.isdigit() else 0)
        if action == "toggle":
            if not case.is_active and not case.items.filter(Q(weight__isnull=True) | Q(weight__gt=0)).exists():
                messages.error(request, f"В кейсе «{case.name}» нет предметов, которые могут выпасть: добавьте их или задайте вес больше 0, прежде чем включать.")
                return redirect("control_center:cases")
            case.is_active = not case.is_active
            case.save(update_fields=["is_active", "updated_at"])
            summary = f"Кейс «{case.name}» {'включён' if case.is_active else 'выключен'}"
        elif action == "delete":
            if case.openings.exists() or case.terminal_sessions.exists() or case.daily_free_cases.exists() or case.promo_codes.exists() or case.daily_bonus_rewards.exists():
                messages.error(request, f"Кейс «{case.name}» уже открывали, выдавали бесплатно или он привязан к промокоду или бонусу дня, удалить его нельзя. Выключите его.")
                return redirect("control_center:cases")
            summary = f"Кейс «{case.name}» удалён"
            case.delete()
        else:
            return redirect("control_center:cases")
        log_admin_action(actor=request.user, action=AuditAction.CASE_SIM_UPDATE, summary=summary[:255], payload={"case": raw_id, "action": action}, request=request)
        messages.success(request, summary + ".")
        return redirect("control_center:cases")

    kind = (request.GET.get("kind") or "").strip()
    qs = SimCase.objects.all()
    if kind in SimCase.Kind.values:
        qs = qs.filter(kind=kind)
    cases = list(qs.order_by("kind", "sort_order", "id"))
    # Separate grouped counts: one annotate over three reverse joins multiplies rows.
    ids = [case.pk for case in cases]
    counters = {
        "items_count": SimCaseItem.objects.filter(case_id__in=ids),
        "openings_count": SimOpening.objects.filter(case_id__in=ids),
        "free_count": DailyFreeCase.objects.filter(case_id__in=ids),
        "promo_count": PromoCode.objects.filter(case_id__in=ids),
        "reward_count": DailyBonusReward.objects.filter(case_id__in=ids),
    }
    for attr, rows in counters.items():
        counts = dict(rows.values("case_id").annotate(n=Count("id")).values_list("case_id", "n"))
        for case in cases:
            setattr(case, attr, counts.get(case.pk, 0))
    context = {
        "role_access": role_access,
        "cases": cases,
        "kind": kind,
        "kind_options": SimCase.Kind.choices,
        "totals": {
            "all": SimCase.objects.count(),
            "active": SimCase.objects.filter(is_active=True).count(),
        },
    }
    return _render(request, partial="control_center/partials/cases_content.html", context=context, title="Кейсы · Control Center", tab="cases")


@login_required
def sim_case_edit(request, case_id: int | None = None):
    from django.db import transaction

    from modules.v2.case_simulator.forms import SimCaseForm, SimCaseItemFormSet
    from modules.v2.case_simulator.models import SimCase

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    case = get_object_or_404(SimCase, pk=case_id) if case_id else None
    instance = case or SimCase()
    form = SimCaseForm(request.POST or None, request.FILES or None, instance=instance)
    formset = SimCaseItemFormSet(request.POST or None, instance=instance, prefix="items")
    if request.method == "POST":
        # The case form goes first: it copies is_active onto the instance the formset checks.
        form_ok = form.is_valid()
        if form_ok and formset.is_valid():
            is_new = case is None
            with transaction.atomic():
                saved = form.save()
                formset.instance = saved
                formset.save()
            changed = sorted(form.changed_data)
            items_changed = len(formset.new_objects) + len(formset.changed_objects) + len(formset.deleted_objects)
            log_admin_action(
                actor=request.user,
                action=AuditAction.CASE_SIM_UPDATE,
                summary=(f"Кейс «{saved.name}» {'создан' if is_new else 'изменён'}" + (f", предметов изменено: {items_changed}" if items_changed else ""))[:255],
                payload={
                    "case": saved.pk,
                    "changed": changed,
                    "items_added": len(formset.new_objects),
                    "items_changed": len(formset.changed_objects),
                    "items_deleted": len(formset.deleted_objects),
                },
                request=request,
            )
            messages.success(request, f"Кейс «{saved.name}» {'создан' if is_new else 'сохранён'}.")
            return redirect("control_center:case_edit", case_id=saved.pk)
        messages.error(request, "Проверьте поля формы.")
    context = {
        "role_access": role_access,
        "form": form,
        "formset": formset,
        "case": case,
        "economy": _case_economy(case, list(case.items.all())) if case else None,
        "openings_count": case.openings.count() if case else 0,
    }
    title = f"{case.name} · Кейсы" if case else "Новый кейс"
    return _render(request, partial="control_center/partials/case_edit_content.html", context=context, title=title, tab="cases")


# --------------------------------------------------------------------------- market


@login_required
def market_deals(request):
    from modules.v2.market.models import Deal, Listing
    from modules.v2.market.payouts import pending_queue as payout_queue

    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    qs = Deal.objects.select_related("listing", "buyer", "buyer__profile_v2", "seller", "seller__profile_v2")
    if status in Deal.Status.values:
        qs = qs.filter(status=status)
    if q:
        filters = Q(code__iexact=q) | Q(listing__market_hash_name__icontains=q) | Q(buyer__username__icontains=q) | Q(seller__username__icontains=q)
        qs = qs.filter(filters)
    page_obj = Paginator(qs.order_by("-created_at", "-id"), 50).get_page(request.GET.get("page") or 1)
    totals = Deal.objects.aggregate(
        open=Count("id", filter=Q(status__in=Deal.OPEN_STATUSES)),
        disputed=Count("id", filter=Q(status=Deal.Status.DISPUTED)),
        completed=Count("id", filter=Q(status=Deal.Status.COMPLETED)),
        turnover=Sum("price", filter=Q(status=Deal.Status.COMPLETED)),
        frozen=Sum("price", filter=Q(status__in=Deal.OPEN_STATUSES)),
    )
    context = {
        "role_access": role_access,
        "page_obj": page_obj,
        "filters": {"status": status, "q": q},
        "status_options": Deal.Status.choices,
        "totals": totals,
        "active_listings": Listing.objects.filter(status=Listing.Status.ACTIVE).count(),
        "pagination_query": _querystring(request),
        "payout_queue": list(payout_queue()),
    }
    return _render(request, partial="control_center/partials/market_content.html", context=context, title="Рынок · Control Center", tab="market")


@login_required
@require_POST
def market_payout_process(request, code: str):
    from modules.v2.market.payouts import PayoutError, process_payout

    if _admin_access(request) is None:
        return _forbidden()
    paid = request.POST.get("decision") == "paid"
    try:
        payout = process_payout(request.user, code, paid=paid, note=request.POST.get("note", ""), request=request)
    except PayoutError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{payout.code}: {payout.get_status_display().lower()}.")
    return redirect("control_center:market")
