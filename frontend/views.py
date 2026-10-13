from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.db.models import Count, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from common.errors import render_not_found_page
from modules.v2.farm.models import FarmAccount
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.presence import ONLINE_THRESHOLD_MINUTES
from modules.v2.profiles.rbac import get_user_role
from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory, UserPriceAlertRule
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

User = get_user_model()
ROLE_LABELS = {
    UserRole.USER: "User",
    UserRole.FARMER: "Farmer",
    UserRole.MOD: "Moderator",
    UserRole.ADMIN: "Admin",
    UserRole.CHIEF_ADMIN: "Chief admin",
    UserRole.SUPER_ADMIN: "Super admin",
}
ROLE_LABELS_RU = {
    UserRole.USER: "Юзер",
    UserRole.FARMER: "Фермер",
    UserRole.MOD: "Модер",
    UserRole.ADMIN: "Админ",
    UserRole.CHIEF_ADMIN: "Зам. руководителя",
    UserRole.SUPER_ADMIN: "Руководитель",
}


HOME_TRACKER_LIMIT = 4
HOME_TRACKER_WINDOW_DAYS = 7


def _build_home_tracker() -> list[dict[str, object]]:
    window_start = timezone.now() - timedelta(days=HOME_TRACKER_WINDOW_DAYS)
    items = (
        SteamItem.objects
        .filter(is_active=True, price_rub__isnull=False, price_rub__gt=0)
        .order_by("-price_rub")[:HOME_TRACKER_LIMIT]
    )
    rows = []
    for item in items:
        baseline = (
            SteamItemPriceHistory.objects
            .filter(item=item, created_at__lte=window_start)
            .order_by("-created_at")
            .values_list("price_rub", flat=True)
            .first()
        )
        change_percent = None
        if baseline:
            change_percent = (item.price_rub - baseline) / baseline * 100
        rows.append(
            {
                "item": item,
                "icon": item.icon_cdn(128),
                "change_percent": change_percent,
            }
        )
    return rows


def home(request):
    context = {
        "home_tracker": [],
        "home_stats": {},
    }
    try:
        context["home_tracker"] = _build_home_tracker()
        context["home_stats"] = {
            "items": SteamItem.objects.filter(is_active=True).count(),
            "users": User.objects.filter(is_active=True).count(),
            "alerts": UserPriceAlertRule.objects.filter(status=UserPriceAlertRule.Status.ACTIVE).count(),
        }
    except DatabaseError:
        pass
    return render(request, "frontend/home.html", context)


def _build_online_role_stats() -> list[dict[str, object]]:
    threshold = timezone.now() - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    online_counts = {
        row["role"]: row["total"]
        for row in (
            UserProfile.objects
            .filter(last_seen_at__gte=threshold)
            .values("role")
            .annotate(total=Count("id"))
        )
    }

    return [
        {
            "code": role_code,
            "label": ROLE_LABELS[role_code],
            "label_ru": ROLE_LABELS_RU[role_code],
            "total": online_counts.get(role_code, 0),
        }
        for role_code in (
            UserRole.USER,
            UserRole.FARMER,
            UserRole.MOD,
            UserRole.ADMIN,
            UserRole.CHIEF_ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ]


def _dashboard_stats():
    total_users = User.objects.count()
    total_profiles = UserProfile.objects.count()
    staff_users = UserProfile.objects.filter(
        role__in=[UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN, UserRole.MOD]
    ).count()

    steam_items_total = SteamItem.objects.count()
    steam_items_review = SteamItem.objects.filter(needs_review=True).count()
    steam_stock_total = SteamItem.objects.aggregate(total=Sum("stock")).get("total") or 0

    farm_accounts_total = FarmAccount.objects.count()
    farm_accounts_active = FarmAccount.objects.filter(status=FarmAccount.Status.ACTIVE).count()

    latest_users = (
        UserProfile.objects
        .select_related("user")
        .order_by("-created_at")[:6]
    )

    latest_items = SteamItem.objects.order_by("-updated_at")[:6]
    latest_ledger = LedgerEntry.objects.select_related("user", "user__profile_v2").order_by("-created_at")[:8]
    online_role_stats = _build_online_role_stats()
    online_total = sum(row["total"] for row in online_role_stats)
    for row in online_role_stats:
        row["percent"] = round(row["total"] * 100 / online_total, 2) if online_total else 0

    now = timezone.now()
    users_new_week = UserProfile.objects.filter(created_at__gte=now - timedelta(days=7)).count()
    ledger_day = LedgerEntry.objects.filter(created_at__gte=now - timedelta(hours=24)).aggregate(
        count=Count("id"),
        net=Sum("delta"),
    )
    farm_accounts_active_percent = (
        round(farm_accounts_active * 100 / farm_accounts_total) if farm_accounts_total else 0
    )

    return {
        "total_users": total_users,
        "total_profiles": total_profiles,
        "staff_users": staff_users,
        "steam_items_total": steam_items_total,
        "steam_items_review": steam_items_review,
        "steam_stock_total": steam_stock_total,
        "farm_accounts_total": farm_accounts_total,
        "farm_accounts_active": farm_accounts_active,
        "latest_users": latest_users,
        "latest_items": latest_items,
        "latest_ledger": latest_ledger,
        "online_role_stats": online_role_stats,
        "online_total": online_total,
        "users_new_week": users_new_week,
        "ledger_day_count": ledger_day["count"] or 0,
        "ledger_day_net": ledger_day["net"] or 0,
        "farm_accounts_active_percent": farm_accounts_active_percent,
    }


@login_required
def dashboard_preview(request):
    context = {
        "role_access": get_user_role(request.user),
        **_dashboard_stats(),
    }
    return render_dashboard_page(
        request,
        full_template="frontend/dashboard_preview.html",
        partial_template="frontend/partials/dashboard_preview_content.html",
        context=context,
        page_title=build_page_title("Dashboard"),
        active_menu_key="dashboard_preview",
    )


@login_required
def admin_panel(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Control Center доступен только admin+.")

    recent_admin_adjustments = (
        LedgerEntry.objects
        .select_related("user", "user__profile_v2")
        .filter(reason=LedgerReason.ADMIN_ADJUST)
        .order_by("-created_at")[:12]
    )

    top_profiles = (
        UserProfile.objects
        .select_related("user")
        .order_by("-balance_coins", "-updated_at")[:8]
    )

    role_distribution = (
        UserProfile.objects
        .values("role")
        .annotate(total=Count("id"))
        .order_by("-total", "role")
    )

    context = {
        "role_access": role_access,
        "recent_admin_adjustments": recent_admin_adjustments,
        "top_profiles": top_profiles,
        "role_distribution": role_distribution,
        **_dashboard_stats(),
    }
    return render_dashboard_page(
        request,
        full_template="frontend/admin_panel.html",
        partial_template="frontend/partials/admin_panel_content.html",
        context=context,
        page_title=build_page_title("Control Center"),
        active_menu_key="control_center",
    )


def testing_preview(request):
    return render(request, "frontend/testing_preview.html")



def not_found_page(request, exception=None):
    return render_not_found_page(request)
