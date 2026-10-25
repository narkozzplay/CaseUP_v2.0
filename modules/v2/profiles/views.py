from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from modules.v2.accounts.steam_sync import (
    get_steam_social_auth,
    sync_profile_from_social_auth,
    sync_profile_from_steam_api,
)
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.profiles.services.faceit import sync_faceit_if_needed, sync_faceit_profile
from modules.v2.profiles.services.presence import is_profile_online
from modules.v2.profiles.services.steam_profile import sync_profile_steam_meta_if_needed
from modules.v2.profile_customization.services import (
    can_customize_profile,
    get_or_create_customization,
    get_profile_effect_classes,
)
from modules.v2.social.services import build_space_url, get_or_create_direct_space
from modules.v2.social_friends.forms import PrivacySettingsForm as SocialPrivacySettingsForm
from modules.v2.social_friends.services import (
    can_view_profile,
    get_friendship_context,
    get_or_create_privacy_settings,
    update_privacy_settings,
)
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
    AdminBalanceAdjustForm,
    AdminFarmerSubscriptionForm,
    AdminProfileIdentityForm,
    AdminSiteSubscriptionForm,
    UserAvatarUploadForm,
    UserThemePreferencesForm,
)
from .models import LedgerEntry, LedgerReason, UserProfile, UserRole, VerificationLevel, VerificationRequest
from .presentation import get_profile_avatar_url, get_profile_display_name, get_verification_badge
from .rbac import can_manage_user, get_user_role

User = get_user_model()

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

    _ADMIN_ROLES = {UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN}

    changed = False
    if profile.role != normalized_role:
        profile.role = normalized_role
        update_fields = ["role", "updated_at"]
        # Auto-assign Legend verification to admin-level roles
        if normalized_role in _ADMIN_ROLES and profile.verification_level != VerificationLevel.LEGEND:
            profile.verification_level = VerificationLevel.LEGEND
            update_fields.append("verification_level")
        profile.save(update_fields=update_fields)
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


def _steam_connect_state(user) -> dict | None:
    """For the owner without Steam: whether the connect form must also ask for the legal consent."""
    from modules.v2.accounts.consent import user_has_current_consent

    if get_steam_social_auth(user) is not None:
        return None
    return {"needs_consent": not user_has_current_consent(user)}


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
    profile_verification_badge = get_verification_badge(profile)

    if can_manage_user(request.user, target_user) and can_view_private_profile:
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

    friendship_ctx = (
        get_friendship_context(request.user, target_user)
        if not is_owner and getattr(request.user, "is_authenticated", False)
        else {}
    )

    # Effective background: catalog override takes priority over Steam background
    effective_bg_static = (
        str(profile.selected_bg_static_url or "").strip()
        or str(profile.steam_profile_background or "").strip()
    )
    effective_bg_video = (
        str(profile.selected_bg_video_url or "").strip()
        or str(profile.steam_profile_background_video or "").strip()
    )

    return {
        "profile": profile,
        "profile_user": target_user,
        "profile_display_name": profile_display_name,
        "profile_avatar_url": profile_avatar_url,
        "profile_is_online": is_profile_online(profile),
        "recent_entries": recent_entries,
        "role_access": target_role_access,
        "viewer_role_access": viewer_role_access,
        "is_owner_profile": is_owner,
        "steam_connect": _steam_connect_state(request.user) if is_owner and not profile.steam_id else None,
        "can_view_private_profile": can_view_private_profile,
        "friendship_context": friendship_ctx,
        "effective_bg_static": effective_bg_static,
        "effective_bg_video": effective_bg_video,
        "profile_effects": get_profile_effect_classes(target_user),
        "profile_verification_badge": profile_verification_badge,
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


LEDGER_REASON_LABELS_RU = {
    LedgerReason.TOPUP: "Пополнение",
    LedgerReason.CASE_OPEN: "Открытие кейса",
    LedgerReason.ITEM_SALE: "Продажа предмета",
    LedgerReason.WITHDRAW_LOCK: "Заявка на вывод",
    LedgerReason.WITHDRAW_CANCEL: "Отмена вывода",
    LedgerReason.ADMIN_ADJUST: "Правка админом",
    LedgerReason.SUBSCRIPTION_PURCHASE: "Покупка подписки",
    LedgerReason.THEME_PURCHASE: "Покупка темы",
    LedgerReason.STORE_PURCHASE: "Покупка в магазине",
    LedgerReason.BUNDLE_PURCHASE: "Покупка набора",
    LedgerReason.REFERRAL_BONUS: "Бонус за рефералов",
    LedgerReason.DAILY_BONUS: "Ежедневный бонус",
    LedgerReason.MARKET_PURCHASE: "Покупка на рынке",
    LedgerReason.MARKET_REFUND: "Возврат по сделке",
    LedgerReason.MARKET_SALE: "Продажа на рынке",
    LedgerReason.MARKET_PAYOUT: "Вывод с рынка",
    LedgerReason.PROMO_BONUS: "Бонус по промокоду",
    LedgerReason.DONATION: "Донат проекту",
}


def _build_settings_context(*, request, target_user):
    """Build context for profile settings page (avatar, themes, privacy, customization, admin)."""
    profile, _ = UserProfile.objects.get_or_create(user=target_user)
    is_owner = request.user.id == target_user.id
    viewer_role_access = get_user_role(request.user)
    target_role_access = get_user_role(target_user)
    can_view_private = _can_view_private_profile(request_user=request.user, target_user=target_user)

    profile_display_name = get_profile_display_name(profile, fallback_username=target_user.username)
    profile_avatar_url = get_profile_avatar_url(profile)
    profile_verification_badge = get_verification_badge(profile)

    # Verification request context (owner only)
    verification_pending_request = None
    if is_owner and profile.verification_level == VerificationLevel.NONE:
        verification_pending_request = (
            VerificationRequest.objects.filter(user=target_user, status=VerificationRequest.Status.PENDING)
            .order_by("-created_at")
            .first()
        )

    # Avatar upload (owner only)
    avatar_upload_form = UserAvatarUploadForm(instance=profile) if is_owner else None

    # Theme preferences (owner only)
    user_theme_preferences_form = None
    if is_owner:
        user_theme_preferences_form = UserThemePreferencesForm(
            user=target_user,
            current_preferences=profile.theme_preferences or {},
        )

    # Privacy settings (owner only)
    privacy_form = None
    if is_owner:
        privacy_settings_obj = get_or_create_privacy_settings(target_user)
        privacy_form = SocialPrivacySettingsForm(instance=privacy_settings_obj)

    # Customization
    # Select/clear actions always act on the viewer, so the card is shown on one's own settings only.
    viewer_can_customize = is_owner and can_customize_profile(request.user)
    customization = get_or_create_customization(target_user) if viewer_can_customize else None

    # Admin forms
    admin_identity_form = None
    admin_site_subscription_form = None
    admin_farmer_subscription_form = None
    admin_balance_form = None
    admin_balance_entries = []
    if can_manage_user(request.user, target_user) and can_view_private:
        admin_balance_form = AdminBalanceAdjustForm(current_balance=profile.balance_coins)
        admin_balance_entries = list(
            LedgerEntry.objects.filter(user=target_user)
            .select_related("created_by")
            .order_by("-created_at", "-id")[:6]
        )
        for entry in admin_balance_entries:
            entry.reason_label_ru = LEDGER_REASON_LABELS_RU.get(entry.reason, "")
        active_site_subscription = get_active_subscription(target_user, family=SubscriptionPlan.Family.SITE)
        active_farmer_subscription = get_active_subscription(target_user, family=SubscriptionPlan.Family.FARMER)
        admin_identity_form = AdminProfileIdentityForm(
            target_user=target_user,
            target_profile=profile,
            actor_user=request.user,
        )
        admin_site_subscription_form = AdminSiteSubscriptionForm(
            active_site_subscription=active_site_subscription,
        )
        show_farmer = target_role_access.is_farmer_plus
        if show_farmer:
            admin_farmer_subscription_form = AdminFarmerSubscriptionForm(
                active_farmer_subscription=active_farmer_subscription,
            )

    return {
        "profile": profile,
        "profile_user": target_user,
        "profile_display_name": profile_display_name,
        "profile_avatar_url": profile_avatar_url,
        "profile_verification_badge": profile_verification_badge,
        "role_access": target_role_access,
        "viewer_role_access": viewer_role_access,
        "is_owner_profile": is_owner,
        "can_view_private_profile": can_view_private,
        "avatar_upload_form": avatar_upload_form,
        "user_theme_preferences_form": user_theme_preferences_form,
        "privacy_form": privacy_form,
        "viewer_can_customize": viewer_can_customize,
        "customization": customization,
        "verification_pending_request": verification_pending_request,
        "admin_identity_form": admin_identity_form,
        "admin_site_subscription_form": admin_site_subscription_form,
        "admin_farmer_subscription_form": admin_farmer_subscription_form,
        "admin_balance_form": admin_balance_form,
        "admin_balance_entries": admin_balance_entries,
        "show_farmer_subscription_zone": target_role_access.is_farmer_plus,
    }


@login_required
def profile_settings_view(request):
    """Settings page for own profile. Privacy form handled here; admin forms go to profile_admin_update."""
    if request.method == "POST" and "verification_request_submit" in request.POST:
        requested_level = (request.POST.get("requested_level") or "").strip()
        user_message = (request.POST.get("user_message") or "").strip()[:500]
        valid_levels = {VerificationRequest.RequestedLevel.VERIFIED, VerificationRequest.RequestedLevel.FARMER}
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if requested_level not in valid_levels:
            messages.error(request, "Неверный уровень верификации.")
        elif profile.verification_level != VerificationLevel.NONE:
            messages.info(request, "У вас уже есть верификация.")
        elif VerificationRequest.objects.filter(user=request.user, status=VerificationRequest.Status.PENDING).exists():
            messages.warning(request, "У вас уже есть активная заявка на рассмотрении.")
        else:
            VerificationRequest.objects.create(
                user=request.user,
                requested_level=requested_level,
                user_message=user_message,
            )
            messages.success(request, "Заявка на верификацию подана. Мы рассмотрим её в ближайшее время.")
        return redirect("profiles:profile_settings")

    if request.method == "POST" and "privacy_settings_submit" in request.POST:
        privacy_settings_obj = get_or_create_privacy_settings(request.user)
        form = SocialPrivacySettingsForm(request.POST, instance=privacy_settings_obj)
        if form.is_valid():
            update_privacy_settings(
                request.user,
                who_can_send_friend_request=form.cleaned_data["who_can_send_friend_request"],
                who_can_send_direct=form.cleaned_data["who_can_send_direct"],
                who_can_see_online=form.cleaned_data["who_can_see_online"],
                who_can_see_profile=form.cleaned_data["who_can_see_profile"],
            )
            messages.success(request, "Настройки приватности сохранены.")
        else:
            messages.error(request, "Ошибка сохранения настроек приватности.")
        return redirect("profiles:profile_settings")

    context = _build_settings_context(request=request, target_user=request.user)
    from modules.v2.webpush.services import settings_context as push_settings_context

    context["push_settings"] = push_settings_context(request.user)
    profile_display_name = context["profile_display_name"]
    return render_dashboard_page(
        request,
        full_template="profiles/settings.html",
        partial_template="profiles/partials/settings_content.html",
        context=context,
        page_title=build_page_title(f"Настройки — {profile_display_name}"),
        active_menu_key="profile",
    )


@login_required
def profile_user_settings_view(request, user_id: int):
    """Admin settings page for another user's profile."""
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Настройки чужого профиля доступны только admin+.")
    target_user = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
    ).user
    context = _build_settings_context(request=request, target_user=target_user)
    profile_display_name = context["profile_display_name"]
    return render_dashboard_page(
        request,
        full_template="profiles/settings.html",
        partial_template="profiles/partials/settings_content.html",
        context=context,
        page_title=build_page_title(f"Настройки — {profile_display_name}"),
        active_menu_key="profile",
    )


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


PROFILE_CLOSED_REASONS = {
    "privacy_friends_only": {
        "ru": ("Профиль виден только друзьям", "Пользователь открыл профиль только для друзей. Отправь заявку в друзья, и после её принятия профиль откроется."),
        "en": ("Profile is visible to friends only", "This user shares the profile with friends only. Send a friend request, and the profile opens once it is accepted."),
    },
    "privacy_nobody": {
        "ru": ("Профиль скрыт", "Пользователь закрыл свой профиль от всех. Посмотреть его сейчас нельзя."),
        "en": ("Profile is hidden", "This user has hidden the profile from everyone. It can't be viewed right now."),
    },
    "blocked_by_target": {
        "ru": ("Профиль недоступен", "Пользователь ограничил тебе доступ к своему профилю."),
        "en": ("Profile unavailable", "This user has restricted your access to the profile."),
    },
    "inactive": {
        "ru": ("Аккаунт неактивен", "Этот аккаунт отключён, его профиль сейчас недоступен."),
        "en": ("Account is inactive", "This account is disabled, so its profile is unavailable."),
    },
}


def _render_profile_closed(request, *, target_user, reason: str):
    profile, _ = UserProfile.objects.get_or_create(user=target_user)
    lang = "en" if str(getattr(request, "LANGUAGE_CODE", "ru") or "ru").lower().startswith("en") else "ru"
    title, description = PROFILE_CLOSED_REASONS.get(reason, PROFILE_CLOSED_REASONS["privacy_nobody"])[lang]
    # A blocked viewer sees no identity at all; other reasons keep name and avatar like a private Steam profile.
    show_identity = reason not in {"blocked_by_target", "inactive"}
    display_name = get_profile_display_name(profile, fallback_username=target_user.username)
    context = {
        "profile_user": target_user,
        "profile_display_name": display_name if show_identity else "",
        "profile_avatar_url": get_profile_avatar_url(profile) if show_identity else "",
        "profile_closed_reason": reason,
        "profile_closed_title": title,
        "profile_closed_description": description,
        "friendship_context": get_friendship_context(request.user, target_user) if reason == "privacy_friends_only" else {},
    }
    response = render_dashboard_page(
        request,
        full_template="profiles/profile_closed.html",
        partial_template="profiles/partials/profile_closed_content.html",
        context=context,
        page_title=build_page_title(title),
        active_menu_key="profile",
    )
    response.status_code = 403
    return response


@login_required
def profile_detail_view(request, user_id: int):
    target_user = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
    ).user
    if request.user.id != target_user.id and not can_view_foreign_profiles(request.user):
        allowed, reason = can_view_profile(request.user, target_user)
        if not allowed:
            return _render_profile_closed(request, target_user=target_user, reason=reason)
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
    if not can_manage_user(request.user, target_user):
        return HttpResponseForbidden("Нельзя редактировать собственный профиль или пользователя своего уровня и выше.")
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
    balance_form = AdminBalanceAdjustForm(current_balance=profile.balance_coins)

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

            elif section == "balance":
                profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
                balance_form = AdminBalanceAdjustForm(
                    request.POST,
                    current_balance=profile.balance_coins,
                )
                if not balance_form.is_valid():
                    raise ValueError("balance_form_invalid")

                balance_delta = balance_form.cleaned_data["balance_delta"]
                entry = adjust_balance(
                    user=target_user,
                    delta=balance_delta,
                    reason=LedgerReason.ADMIN_ADJUST,
                    amount_snapshot=balance_delta,
                    idempotency_key=f"profile-admin-balance:{target_user.id}:{uuid.uuid4()}",
                    comment=balance_form.cleaned_data["balance_comment"],
                    created_by=request.user,
                )
                sign = "+" if balance_delta > 0 else "−"
                messages.success(
                    request,
                    f"Баланс изменён: {sign}{abs(balance_delta):.2f}, теперь {entry.balance_after:.2f}.",
                )

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
        if section == "balance":
            if request.user.id == target_user.id:
                settings_url = reverse("profiles:profile_settings")
            else:
                settings_url = reverse("profiles:profile_user_settings", args=[target_user.id])
            return redirect(f"{settings_url}#settings-admin-balance")
        redirect_name = _profile_redirect_name(request.user, target_user)
        if redirect_name == "profiles:profile":
            return redirect(redirect_name)
        return redirect(redirect_name, user_id=target_user.id)

    context = _build_settings_context(request=request, target_user=target_user)
    context["admin_identity_form"] = identity_form
    context["admin_site_subscription_form"] = site_form
    if context.get("admin_farmer_subscription_form") is not None:
        context["admin_farmer_subscription_form"] = farmer_form
    context["admin_balance_form"] = balance_form
    return render_dashboard_page(
        request,
        full_template="profiles/settings.html",
        partial_template="profiles/partials/settings_content.html",
        context=context,
        page_title=build_page_title(f"Настройки — {context['profile_display_name']}"),
        active_menu_key="profile",
    )


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
    return redirect("profiles:profile_settings")


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


@login_required
def profile_start_direct_chat_view(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    target_user = get_object_or_404(User, pk=user_id, is_active=True)
    if target_user.pk == request.user.pk:
        return HttpResponseForbidden("Нельзя начать чат с самим собой.")

    try:
        space = get_or_create_direct_space(request.user, target_user)
    except PermissionError as exc:
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER") or "profiles:profile_detail", user_id)
    return redirect(build_space_url(space))


@login_required
def verification_requests_view(request):
    """Страница рассмотрения заявок на верификацию. Доступна mod+."""
    from django.utils import timezone as tz

    role_access = get_user_role(request.user)
    if not role_access.is_moder_plus:
        return HttpResponseForbidden("Доступ закрыт. Требуется роль Модератора или выше.")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        req_id = (request.POST.get("request_id") or "").strip()
        admin_note = (request.POST.get("admin_note") or "").strip()[:500]

        try:
            ver_req = VerificationRequest.objects.select_for_update().get(
                pk=req_id,
                status=VerificationRequest.Status.PENDING,
            )
        except VerificationRequest.DoesNotExist:
            messages.error(request, "Заявка не найдена или уже обработана.")
            return redirect("profiles:verification_requests")

        if action == "approve":
            ver_req.status = VerificationRequest.Status.APPROVED
            ver_req.admin_note = admin_note
            ver_req.reviewed_by = request.user
            ver_req.reviewed_at = tz.now()
            ver_req.save(update_fields=["status", "admin_note", "reviewed_by", "reviewed_at"])
            UserProfile.objects.filter(user=ver_req.user).update(
                verification_level=ver_req.requested_level,
                updated_at=tz.now(),
            )
            messages.success(request, f"Заявка одобрена — {ver_req.user.username} верифицирован как {ver_req.get_requested_level_display()}.")
        elif action == "deny":
            ver_req.status = VerificationRequest.Status.DENIED
            ver_req.admin_note = admin_note
            ver_req.reviewed_by = request.user
            ver_req.reviewed_at = tz.now()
            ver_req.save(update_fields=["status", "admin_note", "reviewed_by", "reviewed_at"])
            messages.info(request, f"Заявка {ver_req.user.username} отклонена.")
        else:
            messages.error(request, "Неизвестное действие.")

        return redirect("profiles:verification_requests")

    # GET — список
    tab = (request.GET.get("tab") or "pending").strip()
    if tab not in ("pending", "all"):
        tab = "pending"

    if tab == "all":
        qs = (
            VerificationRequest.objects
            .select_related("user", "user__profile_v2", "reviewed_by")
            .order_by("-created_at")[:200]
        )
    else:
        qs = (
            VerificationRequest.objects
            .filter(status=VerificationRequest.Status.PENDING)
            .select_related("user", "user__profile_v2", "reviewed_by")
            .order_by("created_at")
        )

    rows = []
    for req in qs:
        profile = getattr(req.user, "profile_v2", None)
        rows.append({
            "request": req,
            "user": req.user,
            "user_id": req.user_id,
            "display_name": get_profile_display_name(profile, fallback_username=req.user.username),
            "avatar_url": get_profile_avatar_url(profile),
            "role_label": profile.get_role_display() if profile else "—",
            "verification_badge": get_verification_badge(profile),
            "profile_url": reverse("profiles:profile_detail", args=[req.user_id]),
            "reviewed_by_name": req.reviewed_by.username if req.reviewed_by else "—",
        })

    pending_count = VerificationRequest.objects.filter(status=VerificationRequest.Status.PENDING).count()

    return render_dashboard_page(
        request,
        full_template="profiles/verification_requests.html",
        partial_template="profiles/partials/verification_requests_content.html",
        context={
            "rows": rows,
            "tab": tab,
            "pending_count": pending_count,
            "viewer_is_admin": role_access.is_admin_plus,
        },
        page_title=build_page_title("Заявки на верификацию"),
        active_menu_key="verification_requests",
    )
