from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from common.errors import render_not_found_page
from frontend.forms import DevBalanceTopUpForm
from modules.v2.farm.models import FarmAccount
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

User = get_user_model()
ONLINE_WINDOW_MINUTES = 5

ROLE_LABELS = {
    UserRole.USER: "User",
    UserRole.FARMER: "Farmer",
    UserRole.MOD: "Moderator",
    UserRole.ADMIN: "Admin",
    UserRole.CHIEF_ADMIN: "Chief admin",
    UserRole.SUPER_ADMIN: "Super admin",
}


def home(request):
    return render(request, "frontend/home.html")


def _build_online_role_stats() -> list[dict[str, object]]:
    threshold = timezone.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES)
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
        "online_total": sum(row["total"] for row in online_role_stats),
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

    if request.method == "POST":
        form = DevBalanceTopUpForm(request.POST)
        if form.is_valid():
            target_user = form.cleaned_data["target_user"]
            amount = form.cleaned_data["amount"]

            try:
                adjust_balance(
                    user=target_user,
                    delta=amount,
                    reason=LedgerReason.ADMIN_ADJUST,
                    amount_snapshot=amount,
                    idempotency_key=f"dev-topup:{target_user.id}:{uuid.uuid4()}",
                )
            except BalanceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Balance topped up: {target_user.username} +{amount}",
                )
                return redirect("frontend:admin_panel")
    else:
        form = DevBalanceTopUpForm()

    recent_admin_adjustments = (
        LedgerEntry.objects
        .select_related("user")
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
        "form": form,
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
