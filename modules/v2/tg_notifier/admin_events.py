"""Personal Telegram notices to the head of the site: a new user signed up, a user topped up the balance.

They go to the highest-ranked active admins who linked Telegram (a superuser counts as super admin): if a
super admin has Telegram, only super admins get them; if none has, chief admins; then admins. Each kind is
switched on the TG Notifier settings page. Sending runs in Celery after the commit, a little later for
sign-ups so a Steam account already has its nickname.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone

logger = logging.getLogger(__name__)

SIGNUP_DELAY_SECONDS = 30


def head_admin_recipients() -> list:
    """Active admin+ users with Telegram linked, of the highest rank that has any."""
    from django.db.models import Q

    from modules.v2.profiles.models import UserRole
    from modules.v2.profiles.rbac import ROLE_RANK, get_user_role

    candidates = (
        get_user_model()
        .objects.filter(Q(profile_v2__role__in=(UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN)) | Q(is_superuser=True), is_active=True)
        .exclude(profile_v2__telegram_chat_id__isnull=True)
        .exclude(profile_v2__telegram_chat_id="")
        .select_related("profile_v2")
        .distinct()
        .order_by("pk")
    )
    ranked = [(get_user_role(user).rank, user) for user in candidates]
    ranked = [(rank, user) for rank, user in ranked if rank >= ROLE_RANK[UserRole.ADMIN]]
    if not ranked:
        return []
    top = max(rank for rank, _ in ranked)
    return [user for rank, user in ranked if rank == top]


def _money(value) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


def _now_label(moment=None) -> str:
    return timezone.localtime(moment or timezone.now()).strftime("%d.%m.%Y %H:%M") + " МСК"


def _user_link(user_id: int) -> str:
    from django.urls import reverse

    from .services import resolve_public_base_url

    try:
        return f"{resolve_public_base_url()}{reverse('control_center:user_detail', args=[user_id])}"
    except Exception:  # noqa: BLE001
        return ""


def _user_line(user) -> str:
    from modules.v2.profiles.presentation import get_user_display_name

    name = get_user_display_name(user)
    if name != user.username and user.username:
        return f"{name} ({user.username}, ID {user.pk})"
    return f"{name} (ID {user.pk})"


def _signup_method(user) -> str:
    try:
        from social_django.models import UserSocialAuth
    except Exception:  # noqa: BLE001
        return "логин и пароль"
    providers = list(UserSocialAuth.objects.filter(user=user).values_list("provider", flat=True))
    if "steam" in providers:
        return "Steam"
    if providers:
        return ", ".join(providers)
    return "логин и пароль"


def build_signup_message(user) -> str:
    lines = [
        "👤 Новый пользователь на CaseUP",
        "",
        f"Пользователь: {_user_line(user)}",
        f"Вход через: {_signup_method(user)}",
    ]
    referral = getattr(user, "referred_by", None)  # the missing reverse one-to-one raises an AttributeError subclass
    if referral is not None:
        from modules.v2.profiles.presentation import get_user_display_name

        lines.append(f"Пригласил: {get_user_display_name(referral.referrer)} (ID {referral.referrer_id})")
    lines.append(f"Дата: {_now_label(user.date_joined)}")
    lines.append(f"Всего пользователей: {get_user_model().objects.count()}")
    link = _user_link(user.pk)
    if link:
        lines += ["", link]
    return "\n".join(lines)


def build_topup_message(entry) -> str:
    payment = getattr(entry, "payment", None)
    title = "💰 Пополнение баланса"
    if payment is not None and payment.is_test:
        title += " (тестовый платёж)"
    lines = [
        title,
        "",
        f"Пользователь: {_user_line(entry.user)}",
        f"Сумма: +{_money(entry.delta)}",
    ]
    if payment is not None:
        if payment.fee_amount:
            lines.append(f"Оплачено: {_money(payment.amount)} (комиссия {_money(payment.fee_amount)})")
        from modules.v2.payments.providers import PROVIDER_CLASSES

        provider = PROVIDER_CLASSES.get(payment.provider)
        lines.append(f"Способ: {getattr(provider, 'title', payment.provider)}")
    lines.append(f"Баланс после: {_money(entry.balance_after)}")
    lines.append(f"Дата: {_now_label(entry.created_at)}")
    link = _user_link(entry.user_id)
    if link:
        lines += ["", link]
    return "\n".join(lines)


def send_to_head_admins(text: str) -> int:
    """Sends the text to every head admin; returns how many got it."""
    from modules.v2.system.services import is_module_enabled

    from .services import get_telegram_notifier_config, is_enabled, send_message

    if not is_module_enabled("tg_notifier") or not is_enabled(get_telegram_notifier_config()):
        return 0
    sent = 0
    for admin in head_admin_recipients():
        try:
            send_message(text=text, chat_id=admin.profile_v2.telegram_chat_id, timeout=10)
            sent += 1
        except Exception:  # noqa: BLE001
            logger.warning("Admin notice to user %s failed.", admin.pk, exc_info=True)
    return sent


def notify_signup(user_id: int) -> int:
    from .services import get_telegram_notifier_config

    if not get_telegram_notifier_config().admin_signup_notices:
        return 0
    user = get_user_model().objects.filter(pk=user_id).select_related("profile_v2").first()
    if user is None:
        return 0
    return send_to_head_admins(build_signup_message(user))


def notify_topup(entry_id: int) -> int:
    from modules.v2.profiles.models import LedgerEntry

    from .services import get_telegram_notifier_config

    if not get_telegram_notifier_config().admin_topup_notices:
        return 0
    entry = LedgerEntry.objects.filter(pk=entry_id).select_related("user__profile_v2").first()
    if entry is None:
        return 0
    return send_to_head_admins(build_topup_message(entry))


def _queue(task, arg, countdown: int = 0) -> None:
    try:
        task.apply_async(args=[arg], countdown=countdown)
    except Exception:  # noqa: BLE001 - a notice must never break a sign-up or a payment
        logger.warning("Could not queue %s for %s.", task.name, arg, exc_info=True)


def _after_user_saved(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    from .tasks import notify_admins_signup_task

    user_id = instance.pk
    transaction.on_commit(lambda: _queue(notify_admins_signup_task, user_id, SIGNUP_DELAY_SECONDS))


def _after_ledger_entry(sender, instance, created, raw=False, **kwargs):
    from modules.v2.profiles.models import LedgerReason

    if not created or raw or instance.reason != LedgerReason.TOPUP or instance.delta <= 0:
        return
    from .tasks import notify_admins_topup_task

    entry_id = instance.pk
    transaction.on_commit(lambda: _queue(notify_admins_topup_task, entry_id))


def connect_signals() -> None:
    from modules.v2.profiles.models import LedgerEntry

    post_save.connect(_after_user_saved, sender=get_user_model(), dispatch_uid="tg_notifier_admin_signup")
    post_save.connect(_after_ledger_entry, sender=LedgerEntry, dispatch_uid="tg_notifier_admin_topup")
