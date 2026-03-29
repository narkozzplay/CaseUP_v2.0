from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from modules.v2.accounts.steam_sync import (
    sync_profile_from_social_auth,
    sync_profile_from_steam_api,
)
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.profiles.services.faceit import sync_faceit_if_needed, sync_faceit_profile
from modules.v2.profiles.services.steam_profile import sync_profile_steam_meta_if_needed
from modules.v2.subscriptions.services import assign_user_subscription_plan, get_active_subscription, get_subscription_context
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.system.dashboard_shell import render_dashboard_page
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
)
from .models import LedgerEntry, LedgerReason, UserProfile
from .rbac import get_user_role


def _can_view_private_profile(*, request_user, target_user) -> bool:
    if not getattr(request_user, "is_authenticated", False):
        return False
    if request_user.id == target_user.id:
        return True
    viewer_access = get_user_role(request_user)
    return viewer_access.is_admin_plus


def _profile_redirect_name(request_user, target_user) -> str:
    return "profiles:profile" if request_user.id == target_user.id else "profiles:profile_detail"


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

    if viewer_role_access.is_admin_plus and can_view_private_profile:
        admin_identity_form = AdminProfileIdentityForm(
            target_user=target_user,
            target_profile=profile,
        )
        admin_site_subscription_form = AdminSiteSubscriptionForm(
            active_site_subscription=active_site_subscription,
        )
        admin_farmer_subscription_form = AdminFarmerSubscriptionForm(
            active_farmer_subscription=active_farmer_subscription,
        )

    return {
        "profile": profile,
        "profile_user": target_user,
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
    }


@login_required
def profile_view(request):
    context = _build_profile_context(request=request, target_user=request.user)
    return render_dashboard_page(
        request,
        full_template="profiles/profile.html",
        partial_template="profiles/partials/profile_content.html",
        context=context,
        page_title=f"CaseUP | {context['profile'].steam_personaname or context['profile_user'].username}",
        active_menu_key="profile",
    )


@login_required
def profile_detail_view(request, user_id: int):
    target_user = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
    ).user
    context = _build_profile_context(request=request, target_user=target_user)
    return render_dashboard_page(
        request,
        full_template="profiles/profile.html",
        partial_template="profiles/partials/profile_content.html",
        context=context,
        page_title=f"CaseUP | {context['profile'].steam_personaname or context['profile_user'].username}",
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

                messages.success(request, "Основные данные профиля обновлены.")

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
