from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from modules.v2.accounts.steam_sync import (
    sync_profile_from_social_auth,
    sync_profile_from_steam_api,
)
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.profiles.services.faceit import sync_faceit_if_needed, sync_faceit_profile
from modules.v2.profiles.services.steam_profile import sync_profile_steam_meta_if_needed
from modules.v2.store.services import save_user_theme_preferences
from modules.v2.subscriptions.services import assign_user_subscription_plan, get_active_subscription, get_subscription_context
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title
from modules.v2.tg_notifier.services import (
    TelegramNotifierError,
    build_connect_url_for_user,
    get_active_connect_url_for_user,
    unlink_user_telegram,
)

from .forms import (
    AdminFarmerSubscriptionForm,
    AdminProfileIdentityForm,
    AdminSiteSubscriptionForm,
    UserAvatarUploadForm,
    UserThemePreferencesForm,
)
from .models import LedgerEntry, LedgerReason, UserProfile, UserRole
from .presentation import get_profile_avatar_url, get_profile_display_name
from .rbac import get_user_role

USERS_CATALOG_VIEW_MODES = ("grid", "list")
USERS_CATALOG_SORT_OPTIONS = (
    ("newest", "Newest"),
    ("balance_desc", "Balance"),
    ("steam_level_desc", "Steam level"),
    ("role", "Role"),
)
USERS_CATALOG_SORT_MAP = {
    "newest": ("-user__date_joined", "-created_at", "-id"),
    "balance_desc": ("-balance_coins", "-updated_at", "-id"),
    "steam_level_desc": ("-steam_level", "-updated_at", "-id"),
    "role": ("role", "steam_personaname", "user__username", "id"),
}
USERS_CATALOG_PAGE_SIZE = 24


def can_view_foreign_profiles(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    viewer_access = get_user_role(user)
    return bool(viewer_access.is_moder_plus or user.is_staff or user.is_superuser)


def _can_view_private_profile(*, request_user, target_user) -> bool:
    if not getattr(request_user, "is_authenticated", False):
        return False
    if request_user.id == target_user.id:
        return True
    return can_view_foreign_profiles(request_user)


def _profile_redirect_name(request_user, target_user) -> str:
    return "profiles:profile" if request_user.id == target_user.id else "profiles:profile_detail"


def _users_catalog_querystring(request, **overrides: str) -> str:
    query_params = request.GET.copy()
    query_params.pop("page", None)
    for key, value in overrides.items():
        if value:
            query_params[key] = value
        else:
            query_params.pop(key, None)
    return query_params.urlencode()


def _build_user_catalog_card(profile: UserProfile) -> dict[str, object]:
    user = profile.user
    faceit_label = ""
    if profile.faceit_skill_level or profile.faceit_elo:
        faceit_label = f"{profile.faceit_skill_level or 0} / {profile.faceit_elo or 0}"

    activity_kind = "joined"
    activity_at = getattr(user, "date_joined", None)
    if profile.last_seen_at:
        activity_kind = "last_seen"
        activity_at = profile.last_seen_at
    elif getattr(user, "last_login", None):
        activity_kind = "last_login"
        activity_at = user.last_login

    return {
        "profile": profile,
        "user": user,
        "display_name": get_profile_display_name(profile, fallback_username=user.username),
        "avatar_url": get_profile_avatar_url(profile),
        "profile_url": reverse("profiles:profile_detail", args=[user.id]),
        "role_label": profile.get_role_display(),
        "balance": profile.balance_coins,
        "steam_level": profile.steam_level,
        "faceit_label": faceit_label,
        "activity_kind": activity_kind,
        "activity_at": activity_at,
    }


ROLE_TO_FLAGS = {
    UserRole.USER: {"is_staff": False, "is_superuser": False},
    UserRole.FARMER: {"is_staff": False, "is_superuser": False},
    UserRole.MOD: {"is_staff": True, "is_superuser": False},
    UserRole.ADMIN: {"is_staff": True, "is_superuser": False},
    UserRole.CHIEF_ADMIN: {"is_staff": True, "is_superuser": False},
    UserRole.SUPER_ADMIN: {"is_staff": True, "is_superuser": True},
}


def _apply_profile_role(*, target_user, profile: UserProfile, role: str) -> bool:
    normalized_role = (role or "").strip()
    if not normalized_role:
        return False

    changed = False
    if profile.role != normalized_role:
        profile.role = normalized_role
        profile.save(update_fields=["role", "updated_at"])
        changed = True

    desired_flags = ROLE_TO_FLAGS.get(normalized_role, ROLE_TO_FLAGS[UserRole.USER])
    user_changed_fields: list[str] = []
    if bool(target_user.is_staff) != desired_flags["is_staff"]:
        target_user.is_staff = desired_flags["is_staff"]
        user_changed_fields.append("is_staff")
    if bool(target_user.is_superuser) != desired_flags["is_superuser"]:
        target_user.is_superuser = desired_flags["is_superuser"]
        user_changed_fields.append("is_superuser")
    if user_changed_fields:
        target_user.save(update_fields=user_changed_fields)
        changed = True
    return changed


def _build_profile_context(*, request, target_user):
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    is_owner = request.user.id == target_user.id
    can_view_private_profile = _can_view_private_profile(
        request_user=request.user,
        target_user=target_user,
    )
    viewer_role_access = get_user_role(request.user)
    target_role_access = get_user_role(target_user)

    target_site_subscription_context = get_subscription_context(target_user, family=SubscriptionPlan.Family.SITE)
    target_farmer_subscription_context = get_subscription_context(target_user, family=SubscriptionPlan.Family.FARMER)
    active_site_subscription = get_active_subscription(target_user, family=SubscriptionPlan.Family.SITE)
    active_farmer_subscription = get_active_subscription(target_user, family=SubscriptionPlan.Family.FARMER)
    show_farmer_subscription_zone = target_role_access.is_farmer_plus

    if is_owner:
        profile = sync_profile_from_social_auth(request.user, profile=profile, save=True) or profile
        profile = sync_profile_steam_meta_if_needed(profile)
        sync_faceit_if_needed(profile)

    recent_entries = []
    if can_view_private_profile:
        recent_entries = list(
            LedgerEntry.objects
            .filter(user=target_user)
            .order_by("-created_at")[:20]
        )

    active_telegram_connect_url = None
    if is_owner and not profile.telegram_is_linked:
        active_telegram_connect_url = get_active_connect_url_for_user(target_user)

    admin_identity_form = None
    admin_site_subscription_form = None
    admin_farmer_subscription_form = None
    avatar_upload_form = None

    profile_display_name = get_profile_display_name(profile, fallback_username=target_user.username)
    profile_avatar_url = get_profile_avatar_url(profile)

    if viewer_role_access.is_admin_plus and can_view_private_profile:
        admin_identity_form = AdminProfileIdentityForm(
            target_user=target_user,
            target_profile=profile,
            actor_user=request.user,
        )
        admin_site_subscription_form = AdminSiteSubscriptionForm(
            active_site_subscription=active_site_subscription,
        )
        admin_farmer_subscription_form = AdminFarmerSubscriptionForm(
            active_farmer_subscription=active_farmer_subscription,
        )

    user_theme_preferences_form = None
    if is_owner:
        avatar_upload_form = UserAvatarUploadForm(instance=profile)
        user_theme_preferences_form = UserThemePreferencesForm(
            user=target_user,
            current_preferences=profile.theme_preferences or {},
        )

    return {
        "profile": profile,
        "profile_user": target_user,
        "profile_display_name": profile_display_name,
        "profile_avatar_url": profile_avatar_url,
        "recent_entries": recent_entries,
        "role_access": target_role_access,
        "viewer_role_access": viewer_role_access,
        "is_owner_profile": is_owner,
        "can_view_private_profile": can_view_private_profile,
        "active_telegram_connect_url": active_telegram_connect_url,
        "profile_subscription_context": target_site_subscription_context,
        "profile_active_subscription": active_site_subscription,
        "profile_site_subscription_context": target_site_subscription_context,
        "profile_site_active_subscription": active_site_subscription,
        "profile_farmer_subscription_context": target_farmer_subscription_context,
        "profile_farmer_active_subscription": active_farmer_subscription,
        "show_farmer_subscription_zone": show_farmer_subscription_zone,
        "admin_identity_form": admin_identity_form,
        "admin_site_subscription_form": admin_site_subscription_form,
        "admin_farmer_subscription_form": admin_farmer_subscription_form,
        "avatar_upload_form": avatar_upload_form,
        "user_theme_preferences_form": user_theme_preferences_form,
    }


@login_required
def profile_view(request):
    context = _build_profile_context(request=request, target_user=request.user)
    return render_dashboard_page(
        request,
        full_template="profiles/profile.html",
        partial_template="profiles/partials/profile_content.html",
        context=context,
        page_title=build_page_title(context["profile_display_name"]),
        active_menu_key="profile",
    )


@login_required
def users_catalog_view(request):
    if not can_view_foreign_profiles(request.user):
        return HttpResponseForbidden("Каталог пользователей доступен только staff/mod/admin.")

    q = (request.GET.get("q") or "").strip()
    selected_role = (request.GET.get("role") or "").strip()
    selected_sort = (request.GET.get("sort") or "").strip()
    view_mode = (request.GET.get("view") or "").strip()

    valid_roles = {role_code for role_code, _label in UserRole.choices}
    if selected_role not in valid_roles:
        selected_role = ""

    if selected_sort not in USERS_CATALOG_SORT_MAP:
        selected_sort = "newest"

    if view_mode not in USERS_CATALOG_VIEW_MODES:
        view_mode = "grid"

    profiles_qs = UserProfile.objects.select_related("user")

    if q:
        profiles_qs = profiles_qs.filter(
            Q(steam_personaname__icontains=q)
            | Q(user__username__icontains=q)
            | Q(steam_id__icontains=q)
        )

    if selected_role:
        profiles_qs = profiles_qs.filter(role=selected_role)

    profiles_qs = profiles_qs.order_by(*USERS_CATALOG_SORT_MAP[selected_sort])

    paginator = Paginator(profiles_qs, USERS_CATALOG_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    users = [_build_user_catalog_card(profile) for profile in page_obj.object_list]

    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_params["view"] = view_mode
    query_params["sort"] = selected_sort
    if q:
        query_params["q"] = q
    else:
        query_params.pop("q", None)
    if selected_role:
        query_params["role"] = selected_role
    else:
        query_params.pop("role", None)

    context = {
        "page_obj": page_obj,
        "users": users,
        "q": q,
        "selected_role": selected_role,
        "selected_sort": selected_sort,
        "view_mode": view_mode,
        "role_options": UserRole.choices,
        "sort_options": USERS_CATALOG_SORT_OPTIONS,
        "view_modes": USERS_CATALOG_VIEW_MODES,
        "pagination_query": query_params.urlencode(),
        "grid_query": _users_catalog_querystring(request, q=q, role=selected_role, sort=selected_sort, view="grid"),
        "list_query": _users_catalog_querystring(request, q=q, role=selected_role, sort=selected_sort, view="list"),
    }
    return render_dashboard_page(
        request,
        full_template="profiles/users_catalog.html",
        partial_template="profiles/partials/users_catalog_content.html",
        context=context,
        page_title=build_page_title("Users Catalog"),
        active_menu_key="users_catalog",
    )


@login_required
def profile_detail_view(request, user_id: int):
    target_user = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
    ).user
    if request.user.id != target_user.id:
        if not can_view_foreign_profiles(request.user):
            return HttpResponseForbidden("Просмотр чужого профиля доступен только staff/mod/admin.")
    context = _build_profile_context(request=request, target_user=target_user)
    return render_dashboard_page(
        request,
        full_template="profiles/profile.html",
        partial_template="profiles/partials/profile_content.html",
        context=context,
        page_title=build_page_title(context["profile_display_name"]),
        active_menu_key="profile",
    )


@login_required
def profile_admin_update_view(request, user_id: int):
    if request.method != "POST":
        return redirect("profiles:profile_detail", user_id=user_id)

    viewer_role_access = get_user_role(request.user)
    if not viewer_role_access.is_admin_plus:
        return HttpResponseForbidden("Редактирование профиля доступно только admin+.")

    target_user = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
    ).user
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    active_site_subscription = get_active_subscription(
        target_user,
        family=SubscriptionPlan.Family.SITE,
    )
    active_farmer_subscription = get_active_subscription(
        target_user,
        family=SubscriptionPlan.Family.FARMER,
    )

    section = (request.POST.get("admin_section") or "").strip()

    identity_form = AdminProfileIdentityForm(
        target_user=target_user,
        target_profile=profile,
        actor_user=request.user,
    )
    site_form = AdminSiteSubscriptionForm(
        active_site_subscription=active_site_subscription,
    )
    farmer_form = AdminFarmerSubscriptionForm(
        active_farmer_subscription=active_farmer_subscription,
    )

    try:
        with transaction.atomic():
            if section == "identity":
                identity_form = AdminProfileIdentityForm(
                    request.POST,
                    target_user=target_user,
                    target_profile=profile,
                    actor_user=request.user,
                )
                if not identity_form.is_valid():
                    raise ValueError("identity_form_invalid")

                new_username = identity_form.cleaned_data["username"]
                if target_user.username != new_username:
                    target_user.username = new_username
                    target_user.save(update_fields=["username"])

                target_balance = identity_form.cleaned_data["balance_coins"]
                current_balance = profile.balance_coins or Decimal("0.00")
                balance_delta = target_balance - current_balance

                if balance_delta != Decimal("0.00"):
                    adjust_balance(
                        user=target_user,
                        delta=balance_delta,
                        reason=LedgerReason.ADMIN_ADJUST,
                        amount_snapshot=balance_delta,
                        idempotency_key=f"profile-admin-balance:{target_user.id}:{uuid.uuid4()}",
                    )

                role_changed = _apply_profile_role(
                    target_user=target_user,
                    profile=profile,
                    role=identity_form.cleaned_data["role"],
                )
                store_author = bool(identity_form.cleaned_data.get("is_store_author"))
                if profile.is_store_author != store_author:
                    profile.is_store_author = store_author
                    profile.save(update_fields=["is_store_author", "updated_at"])
                messages.success(request, "Основные данные профиля обновлены.")
                if role_changed:
                    messages.success(request, "Роль и staff-права пользователя синхронизированы.")

            elif section == "site_subscription":
                site_form = AdminSiteSubscriptionForm(
                    request.POST,
                    active_site_subscription=active_site_subscription,
                )
                if not site_form.is_valid():
                    raise ValueError("site_form_invalid")

                assign_user_subscription_plan(
                    user=target_user,
                    family=SubscriptionPlan.Family.SITE,
                    plan=site_form.cleaned_data.get("site_subscription_plan"),
                    expires_at=site_form.cleaned_data.get("site_subscription_expires_at"),
                    notes=site_form.cleaned_data.get("site_subscription_notes") or "",
                )

                messages.success(request, "Site-подписка обновлена.")

            elif section == "farmer_subscription":
                farmer_form = AdminFarmerSubscriptionForm(
                    request.POST,
                    active_farmer_subscription=active_farmer_subscription,
                )
                if not farmer_form.is_valid():
                    raise ValueError("farmer_form_invalid")

                assign_user_subscription_plan(
                    user=target_user,
                    family=SubscriptionPlan.Family.FARMER,
                    plan=farmer_form.cleaned_data.get("farmer_subscription_plan"),
                    expires_at=farmer_form.cleaned_data.get("farmer_subscription_expires_at"),
                    notes=farmer_form.cleaned_data.get("farmer_subscription_notes") or "",
                )

                messages.success(request, "Farmer-подписка обновлена.")

            else:
                messages.error(request, "Неизвестный блок обновления профиля.")

    except BalanceError as exc:
        messages.error(request, str(exc))
    except ValueError:
        messages.error(request, "Не удалось сохранить изменения профиля.")
    else:
        redirect_name = _profile_redirect_name(request.user, target_user)
        if redirect_name == "profiles:profile":
            return redirect(redirect_name)
        return redirect(redirect_name, user_id=target_user.id)

    context = _build_profile_context(request=request, target_user=target_user)
    context["admin_identity_form"] = identity_form
    context["admin_site_subscription_form"] = site_form
    context["admin_farmer_subscription_form"] = farmer_form
    return render(request, "profiles/profile.html", context, status=200)


@login_required
def profile_theme_preferences_update_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    form = UserThemePreferencesForm(
        request.POST,
        user=request.user,
        current_preferences=profile.theme_preferences or {},
    )
    if form.is_valid():
        saved_preferences = save_user_theme_preferences(
            user=request.user,
            submitted_preferences=form.get_selected_preferences(),
        )
        if saved_preferences:
            messages.success(request, "Локальные настройки тем сохранены.")
        else:
            messages.success(request, "Локальные темы сброшены на глобальные настройки сайта.")
    else:
        messages.error(request, "Не удалось сохранить настройки локальных тем.")

    return redirect("profiles:profile")


@login_required
def profile_avatar_upload_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    form = UserAvatarUploadForm(request.POST, request.FILES, instance=profile)
    if form.is_valid():
        form.save()
        messages.success(request, "Аватар обновлён.")
        return redirect("profiles:profile")

    messages.error(request, "Не удалось обновить аватар.")
    context = _build_profile_context(request=request, target_user=request.user)
    context["avatar_upload_form"] = form
    return render_dashboard_page(
        request,
        full_template="profiles/profile.html",
        partial_template="profiles/partials/profile_content.html",
        context=context,
        page_title=build_page_title(context["profile_display_name"]),
        active_menu_key="profile",
        current_url=reverse("profiles:profile"),
    )


@login_required
def sync_faceit_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    result = sync_faceit_profile(profile)

    if result.ok:
        messages.success(request, "FACEIT profile synced.")
    else:
        messages.error(request, result.message)

    return redirect("profiles:profile")


@login_required
def sync_steam_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile = sync_profile_from_social_auth(request.user, profile=profile, save=True) or profile

    if not profile.steam_id:
        messages.error(request, "Steam ID is missing.")
        return redirect("profiles:profile")

    before_state = {
        "steam_personaname": profile.steam_personaname or "",
        "steam_avatar": profile.steam_avatar or "",
        "steam_profile_url": profile.steam_profile_url or "",
        "steam_profile_background": profile.steam_profile_background or "",
        "steam_profile_background_video": profile.steam_profile_background_video or "",
        "steam_level": profile.steam_level or 0,
    }

    profile = sync_profile_from_steam_api(profile, save=True)
    profile = sync_profile_steam_meta_if_needed(profile, force=True)

    after_state = {
        "steam_personaname": profile.steam_personaname or "",
        "steam_avatar": profile.steam_avatar or "",
        "steam_profile_url": profile.steam_profile_url or "",
        "steam_profile_background": profile.steam_profile_background or "",
        "steam_profile_background_video": profile.steam_profile_background_video or "",
        "steam_level": profile.steam_level or 0,
    }

    changed = [key for key, value in after_state.items() if before_state.get(key) != value]

    if changed:
        messages.success(request, "Steam profile updated.")
    else:
        messages.info(request, "Steam profile is already up to date.")

    return redirect("profiles:profile")


@login_required
def telegram_connect_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    try:
        connect_url = build_connect_url_for_user(request.user)
    except TelegramNotifierError as exc:
        messages.error(request, f"Не удалось подготовить Telegram-подключение: {exc}")
    else:
        messages.success(request, "Ссылка для подключения Telegram подготовлена.")
        messages.info(
            request,
            f"Откройте бота по ссылке и нажмите Start: {connect_url}",
        )

    return redirect("profiles:profile")


@login_required
def telegram_disconnect_view(request):
    if request.method != "POST":
        return redirect("profiles:profile")

    unlink_user_telegram(request.user)
    messages.success(request, "Telegram отключен от вашего профиля.")
    return redirect("profiles:profile")
