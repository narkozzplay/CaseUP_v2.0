from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole, VerificationRequest
from modules.v2.profiles.services.presence import ONLINE_THRESHOLD_MINUTES

from ..models import AdminAuditLog, UserBan

User = get_user_model()

ROLE_ORDER = (
    UserRole.SUPER_ADMIN,
    UserRole.CHIEF_ADMIN,
    UserRole.ADMIN,
    UserRole.MOD,
    UserRole.FARMER,
    UserRole.USER,
)
ROLE_LABELS_RU = {
    UserRole.USER: "Юзер",
    UserRole.FARMER: "Фермер",
    UserRole.MOD: "Модер",
    UserRole.ADMIN: "Админ",
    UserRole.CHIEF_ADMIN: "Зам. руководителя",
    UserRole.SUPER_ADMIN: "Руководитель",
}
STAFF_ROLES = (UserRole.MOD, UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN)
CHART_DAYS = 14


def _daily_series(rows: dict, *, days: int = CHART_DAYS) -> list[dict[str, object]]:
    today = timezone.localdate()
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        series.append({"date": day, "value": rows.get(day, 0)})
    peak = max((abs(row["value"]) for row in series), default=0)
    for row in series:
        row["percent"] = round(abs(row["value"]) * 100 / peak) if peak else 0
    return series


def registrations_series(days: int = CHART_DAYS) -> list[dict[str, object]]:
    since = timezone.now() - timedelta(days=days)
    rows = {
        row["day"]: row["total"]
        for row in User.objects.filter(date_joined__gte=since)
        .annotate(day=TruncDate("date_joined"))
        .values("day")
        .annotate(total=Count("id"))
    }
    return _daily_series(rows, days=days)


def ledger_volume_series(days: int = CHART_DAYS) -> list[dict[str, object]]:
    since = timezone.now() - timedelta(days=days)
    rows = {
        row["day"]: row["total"]
        for row in LedgerEntry.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
    }
    return _daily_series(rows, days=days)


def role_distribution() -> list[dict[str, object]]:
    counts = {row["role"]: row["total"] for row in UserProfile.objects.values("role").annotate(total=Count("id"))}
    total = sum(counts.values())
    return [
        {
            "code": code,
            "label": ROLE_LABELS_RU[code],
            "total": counts.get(code, 0),
            "percent": round(counts.get(code, 0) * 100 / total) if total else 0,
        }
        for code in ROLE_ORDER
    ]


def build_overview() -> dict[str, object]:
    now = timezone.now()
    online_since = now - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)

    ledger_day = LedgerEntry.objects.filter(created_at__gte=now - timedelta(hours=24)).aggregate(
        count=Count("id"),
        net=Sum("delta"),
        income=Sum("delta", filter=Q(delta__gt=0)),
    )
    from modules.v2.steam_catalog.models import SteamItem

    kpis = {
        "users_total": User.objects.count(),
        "users_new_day": User.objects.filter(date_joined__gte=now - timedelta(hours=24)).count(),
        "users_new_week": User.objects.filter(date_joined__gte=now - timedelta(days=7)).count(),
        "users_online": UserProfile.objects.filter(last_seen_at__gte=online_since).count(),
        "users_banned": UserBan.objects.active().values("user_id").distinct().count(),
        "staff_total": UserProfile.objects.filter(role__in=STAFF_ROLES).count(),
        "verifications_pending": VerificationRequest.objects.filter(status=VerificationRequest.Status.PENDING).count(),
        "catalog_total": SteamItem.objects.count(),
        "catalog_review": SteamItem.objects.filter(needs_review=True).count(),
        "ledger_day_count": ledger_day["count"] or 0,
        "ledger_day_net": ledger_day["net"] or Decimal("0"),
        "ledger_day_income": ledger_day["income"] or Decimal("0"),
        "balances_total": UserProfile.objects.aggregate(total=Sum("balance_coins"))["total"] or Decimal("0"),
        "audit_week": AdminAuditLog.objects.filter(created_at__gte=now - timedelta(days=7)).count(),
    }
    return {
        "kpis": kpis,
        "registrations_series": registrations_series(),
        "ledger_series": ledger_volume_series(),
        "role_rows": role_distribution(),
        "top_profiles": UserProfile.objects.select_related("user").order_by("-balance_coins", "-updated_at")[:6],
        "recent_audit": AdminAuditLog.objects.select_related("actor", "target_user").order_by("-created_at")[:8],
        "recent_admin_adjustments": LedgerEntry.objects.select_related("user", "user__profile_v2")
        .filter(reason=LedgerReason.ADMIN_ADJUST)
        .order_by("-created_at")[:6],
        "latest_users": UserProfile.objects.select_related("user").order_by("-user__date_joined")[:6],
    }
