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
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from modules.v2.accounts.models import TwoFactorAuth
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

    provider = get_provider()
    base_url = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return {
        "provider": provider,
        "webhook_url": f"{base_url}{reverse('payments_public:webhook', args=[provider.code])}" if provider else "",
        "ip_check": webhook_ip_check_enabled(),
        "reconcile_minutes": int(getattr(settings, "CELERY_BEAT_PAYMENTS_RECONCILE_SECONDS", 300)) // 60,
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
        "checked_at": timezone.now(),
    }
    return _render(request, partial="control_center/partials/health_content.html", context=context, title="Система · Control Center", tab="health")


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

    context = {
        "role_access": role_access,
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
