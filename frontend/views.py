from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from common.errors import render_access_denied_page, render_not_found_page
from modules.v2.farm.models import FarmAccount
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.presence import ONLINE_THRESHOLD_MINUTES
from modules.v2.profiles.rbac import get_user_role
from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory, UserPriceAlertRule
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title, get_module_state_map

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
        "home_major": None,
        "home_major_countdown": None,
        "home_feed": [],
    }
    feature_flags = get_module_state_map()
    try:
        if feature_flags.get("majors", True):
            from modules.v2.majors.views import _countdown

            major, _players = _current_major()
            if major is not None:
                context["home_major"] = major
                context["home_major_countdown"] = _countdown(major)
    except DatabaseError:
        pass
    from .activity_feed import build_activity_feed

    viewer_is_team = bool(request.user.is_authenticated and get_user_role(request.user).is_moder_plus)
    context["home_feed"] = build_activity_feed(feature_flags, limit=6, viewer_is_team=viewer_is_team)
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


# Ledger lines a user sees about their own balance, in plain words. Admin corrections stay out.
USER_LEDGER_LABELS_RU = {
    "topup": "Пополнение баланса",
    "case_open": "Открытие кейса",
    "item_sale": "Продажа предмета",
    "withdraw_lock": "Заявка на вывод",
    "withdraw_cancel": "Вывод отменён",
    "subscription_purchase": "Покупка подписки",
    "theme_purchase": "Покупка темы",
    "store_purchase": "Покупка в магазине",
    "bundle_purchase": "Покупка набора",
    "referral_bonus": "Бонус за друга",
    "promo_bonus": "Промокод",
    "daily_bonus": "Бонус дня",
    "market_purchase": "Покупка на Рынке",
    "market_refund": "Возврат с Рынка",
    "market_sale": "Продажа на Рынке",
    "market_payout": "Выплата с Рынка",
    "donation": "Поддержка проекта",
    "major_pickem": "Награда Pick'em",
}
USER_LEDGER_HIDDEN_REASONS = (LedgerReason.ADMIN_ADJUST,)


def _current_major():
    from modules.v2.majors.models import Major, SwissPick

    major = (
        Major.objects.filter(is_published=True, ends_on__gte=timezone.localdate())
        .order_by("starts_at")
        .first()
    )
    if major is None:
        return None, 0
    players = SwissPick.objects.filter(stage__major=major).values("user_id").distinct().count()
    return major, players


def _user_dashboard_context(request) -> dict[str, object]:
    from modules.v2.steam_catalog.models import SteamItemFavorite

    from .activity_feed import build_activity_feed

    user = request.user
    feature_flags = get_module_state_map()
    now = timezone.now()
    threshold = now - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    context: dict[str, object] = {
        "activity_feed": [],
        "my_ledger": [],
        "online_total": 0,
        "market_deals_week": 0,
        "market_lots_active": 0,
        "my_favorites": 0,
        "my_alerts": 0,
        "current_major": None,
        "major_players": 0,
        "price_tracker": [],
    }
    try:
        context["online_total"] = UserProfile.objects.filter(last_seen_at__gte=threshold).count()
        my_ledger = list(
            LedgerEntry.objects.filter(user=user)
            .exclude(reason__in=USER_LEDGER_HIDDEN_REASONS)
            .order_by("-created_at")[:6]
        )
        for entry in my_ledger:
            entry.label_ru = USER_LEDGER_LABELS_RU.get(entry.reason, entry.get_reason_display())
        context["my_ledger"] = my_ledger
        context["my_favorites"] = SteamItemFavorite.objects.filter(user=user).count()
        context["my_alerts"] = UserPriceAlertRule.objects.filter(user=user, status=UserPriceAlertRule.Status.ACTIVE).count()
        if feature_flags.get("market", True):
            from modules.v2.market.models import Deal, Listing

            context["market_deals_week"] = Deal.objects.exclude(status=Deal.Status.CANCELLED).filter(created_at__gte=now - timedelta(days=7)).count()
            context["market_lots_active"] = Listing.objects.filter(status=Listing.Status.ACTIVE).count()
        if feature_flags.get("majors", True):
            context["current_major"], context["major_players"] = _current_major()
        if feature_flags.get("steam_catalog", True):
            context["price_tracker"] = _build_home_tracker()
    except DatabaseError:
        pass
    context["activity_feed"] = build_activity_feed(feature_flags, viewer_is_team=get_user_role(user).is_moder_plus)
    return context


@login_required
def dashboard_preview(request):
    role_access = get_user_role(request.user)
    is_team = role_access.is_moder_plus or request.user.is_staff or request.user.is_superuser
    if is_team:
        context = {"role_access": role_access, "dashboard_mode": "team", **_dashboard_stats()}
        partial_template = "frontend/partials/dashboard_preview_content.html"
    else:
        context = {"role_access": role_access, "dashboard_mode": "user", **_user_dashboard_context(request)}
        partial_template = "frontend/partials/dashboard_user_content.html"
    return render_dashboard_page(
        request,
        full_template="frontend/dashboard_preview.html",
        partial_template=partial_template,
        context=context,
        page_title=build_page_title("Dashboard"),
        active_menu_key="dashboard_preview",
    )


@login_required
def admin_panel(request):
    from modules.v2.control_center.views import overview

    return overview(request)




def _render_legal_document(request, document, template_name: str):
    from django.conf import settings

    from .legal import LEGAL_DOCUMENTS

    return render(
        request,
        template_name,
        {
            "legal_doc": document,
            "legal_documents": LEGAL_DOCUMENTS,
            "site_base_url": (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/"),
        },
    )


def agreement_page(request):
    from .legal import AGREEMENT

    return _render_legal_document(request, AGREEMENT, "legal/agreement.html")


def privacy_page(request):
    from .legal import PRIVACY

    return _render_legal_document(request, PRIVACY, "legal/privacy.html")


def pd_consent_page(request):
    from .legal import PD_CONSENT

    return _render_legal_document(request, PD_CONSENT, "legal/consent.html")


def cookies_page(request):
    from .legal import COOKIES

    return _render_legal_document(request, COOKIES, "legal/cookies.html")


def withdrawal_page(request):
    from .legal import WITHDRAWAL

    return _render_legal_document(request, WITHDRAWAL, "legal/withdrawal.html")


def pickem_rules_page(request):
    from .legal import PICKEM_RULES

    return _render_legal_document(request, PICKEM_RULES, "legal/pickem_rules.html")


def not_found_page(request, exception=None):
    return render_not_found_page(request)


def permission_denied_page(request, exception=None):
    detail = str(exception or "").strip() if exception is not None else ""
    return render_access_denied_page(request, detail=detail)
