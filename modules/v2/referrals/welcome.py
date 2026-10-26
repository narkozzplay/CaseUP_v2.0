"""Two-sided welcome bonus: the inviter and the invited user both get free days of a subscription
(«+7 дней Плюс» by default) after the invited user's first sign-in through Steam.

Anti-abuse:
* one bonus per invited Steam account and side, ever (ReferralWelcomeBonus.steam_id is unique per
  side and survives account deletion);
* only referrals created after the program started (ReferralProgramSettings.welcome_bonus_since);
* a referral on review (shared IP with the inviter, see services._flag_if_shared_ip), accounts that
  share an IP in the antifraud log, or both sitting in one open multi-account case: the bonus is put
  on hold and the referral goes to review in Control Center; the admin's decision releases or
  cancels it;
* a blocked referral, an account in an abusive multi-account group or a banned account: no bonus;
* the inviter gets at most ``referrer_monthly_limit`` bonuses per 30 days.

An active plan of the same or higher level is extended, a lower one is upgraded with its remaining
value credited (subscriptions.services.grant_bonus_days).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Referral, ReferralProgramSettings, ReferralWelcomeBonus

logger = logging.getLogger(__name__)

DEFAULT_PLAN_CODE = "site_plus"
LIMIT_WINDOW = timedelta(days=30)

GRANTED = ReferralWelcomeBonus.Status.GRANTED
HELD = ReferralWelcomeBonus.Status.HELD
DENIED = ReferralWelcomeBonus.Status.DENIED
REFERRER = ReferralWelcomeBonus.Role.REFERRER
REFEREE = ReferralWelcomeBonus.Role.REFEREE


def bonus_plan(config: ReferralProgramSettings | None = None):
    from modules.v2.subscriptions.models import SubscriptionPlan

    config = config or ReferralProgramSettings.load()
    if config.welcome_bonus_plan_id and config.welcome_bonus_plan.is_active:
        return config.welcome_bonus_plan
    return SubscriptionPlan.objects.filter(code=DEFAULT_PLAN_CODE, is_active=True).first()


def steam_id_of(user) -> str:
    from social_django.models import UserSocialAuth

    uid = UserSocialAuth.objects.filter(user=user, provider="steam").values_list("uid", flat=True).first()
    return str(uid or "").strip()


# --------------------------------------------------------------------------- entry points


def on_steam_login(*, user, steam_id: str = "", ip_address: str | None = None) -> list[ReferralWelcomeBonus]:
    """Called on every Steam sign-in (social-auth pipeline). Never raises."""
    try:
        referral = Referral.objects.select_related("referrer", "referee").filter(referee=user).first()
        if referral is None:
            return []
        return award(referral, steam_id=steam_id or steam_id_of(user), ip_address=ip_address)
    except Exception:  # noqa: BLE001 - a sign-in must never fail because of the bonus
        logger.exception("Referral welcome bonus failed for user %s", getattr(user, "pk", None))
        return []


def award(referral: Referral, *, steam_id: str, ip_address: str | None = None) -> list[ReferralWelcomeBonus]:
    """Create (and, if nothing stands in the way, grant) both sides' bonus. Idempotent."""
    steam_id = (steam_id or "").strip()
    config = ReferralProgramSettings.load()
    if not steam_id or not config.welcome_bonus_enabled or referral.created_at < config.welcome_bonus_since:
        return []
    if ReferralWelcomeBonus.objects.filter(steam_id=steam_id).exists() or referral.welcome_bonuses.exists():
        return []  # this Steam account (or this referral) already had its bonus
    plan = bonus_plan(config)
    if plan is None:
        logger.warning("Referral welcome bonus: no active plan %s", DEFAULT_PLAN_CODE)
        return []

    status, reason = _verdict(referral, ip_address=ip_address)
    referrer_status, referrer_reason = status, reason
    if status == GRANTED and _over_monthly_limit(referral.referrer, config):
        referrer_status, referrer_reason = DENIED, f"Лимит: не больше {config.referrer_monthly_limit} бонусов за 30 дней"

    try:
        with transaction.atomic():
            rows = [
                ReferralWelcomeBonus.objects.create(referral=referral, user=referral.referee, role=REFEREE, steam_id=steam_id, days=config.welcome_bonus_days, plan=plan, status=HELD if status == GRANTED else status, reason=reason),
                ReferralWelcomeBonus.objects.create(referral=referral, user=referral.referrer, role=REFERRER, steam_id=steam_id, days=config.welcome_bonus_days, plan=plan, status=HELD if referrer_status == GRANTED else referrer_status, reason=referrer_reason),
            ]
    except IntegrityError:
        return []  # a parallel sign-in got there first

    if status == HELD:
        _send_to_review(referral, reason)
    for row, row_status in zip(rows, (status, referrer_status)):
        if row_status == GRANTED:
            _grant(row)
        else:
            _notify(row)
    _log(referral, rows)
    return rows


def release_held(referral: Referral) -> int:
    """The admin counted a referral on review: grant what was held (the inviter's monthly limit still applies)."""
    from modules.v2.antifraud.models import MultiAccountCase, MultiAccountMember

    released = 0
    abusive = MultiAccountMember.objects.filter(
        user_id__in=(referral.referrer_id, referral.referee_id), case__status=MultiAccountCase.Status.ABUSE
    ).exists()
    for row in ReferralWelcomeBonus.objects.select_related("user", "plan", "referral__referrer", "referral__referee").filter(referral=referral, status=HELD):
        if abusive:
            _deny(row, "Аккаунт в группе мульти-аккаунтов (абуз)")
        elif row.user is None or not row.user.is_active:
            _deny(row, "Аккаунт недоступен")
        else:
            released += bool(_grant(row))
    return released


def deny_held(referral_ids, reason: str) -> int:
    rows = list(ReferralWelcomeBonus.objects.select_related("user", "referral__referrer", "referral__referee").filter(referral_id__in=list(referral_ids), status=HELD))
    for row in rows:
        _deny(row, reason)
    return len(rows)


# --------------------------------------------------------------------------- checks


def _verdict(referral: Referral, *, ip_address: str | None) -> tuple[str, str]:
    if referral.review == Referral.Review.BLOCKED:
        return DENIED, referral.flag_reason or "Реферал отклонён"
    if not referral.referrer.is_active or not referral.referee.is_active:
        return DENIED, "Аккаунт заблокирован"
    abuse = _antifraud_reason(referral, ip_address=ip_address)
    if abuse is not None:
        return abuse
    if referral.review == Referral.Review.FLAGGED:
        return HELD, referral.flag_reason or "Реферал на проверке"
    return GRANTED, ""


def _antifraud_reason(referral: Referral, *, ip_address: str | None) -> tuple[str, str] | None:
    from modules.v2.antifraud.models import MultiAccountCase, MultiAccountMember, UserIp
    from modules.v2.antifraud.services import is_public_ip, shared_ip_limit

    pair = (referral.referrer_id, referral.referee_id)
    if MultiAccountMember.objects.filter(user_id__in=pair, case__status=MultiAccountCase.Status.ABUSE).exists():
        return DENIED, "Аккаунт в группе мульти-аккаунтов (абуз)"
    referrer_cases = set(MultiAccountMember.objects.filter(user_id=referral.referrer_id, case__status=MultiAccountCase.Status.OPEN).values_list("case_id", flat=True))
    if referrer_cases and MultiAccountMember.objects.filter(user_id=referral.referee_id, case_id__in=referrer_cases).exists():
        return HELD, "Аккаунты в одной группе мульти-аккаунтов"

    referee_ips = set(UserIp.objects.filter(user_id=referral.referee_id).values_list("ip", flat=True))
    referee_ips.update(ip for ip in (ip_address, referral.ip_address) if is_public_ip(ip))
    if not referee_ips:
        return None
    common = set(UserIp.objects.filter(user_id=referral.referrer_id, ip__in=referee_ips).values_list("ip", flat=True))
    # An address used by many accounts is a mobile operator or an office NAT, not proof of a multi-account.
    common = {ip for ip in common if UserIp.objects.filter(ip=ip).count() <= shared_ip_limit()}
    if common:
        return HELD, "Общий IP с пригласившим: " + ", ".join(sorted(common)[:3])
    return None


def _over_monthly_limit(user, config: ReferralProgramSettings) -> bool:
    limit = config.referrer_monthly_limit
    if not limit:
        return False
    since = timezone.now() - LIMIT_WINDOW
    return ReferralWelcomeBonus.objects.filter(user=user, role=REFERRER, status=GRANTED, granted_at__gte=since).count() >= limit


def _send_to_review(referral: Referral, reason: str) -> None:
    """Put the referral in the Control Center review queue (admin decisions stick)."""
    if referral.review != Referral.Review.OK or referral.reviewed_at is not None:
        return
    referral.review = Referral.Review.FLAGGED
    referral.flag_reason = reason[:255]
    referral.save(update_fields=["review", "flag_reason"])


# --------------------------------------------------------------------------- grant / deny


def _grant(row: ReferralWelcomeBonus) -> bool:
    from modules.v2.subscriptions.services import grant_bonus_days

    plan = row.plan or bonus_plan()
    if row.user is None or plan is None:
        _deny(row, "Тариф бонуса недоступен")
        return False
    other = row.referral.referee if row.role == REFERRER else row.referral.referrer
    note = f"Реферальный бонус +{row.days} дн. ({'пригласил' if row.role == REFERRER else 'приглашён'}: {other.username})"
    with transaction.atomic():
        # The owner lock serialises this with the user's other grants, so the limit check below
        # cannot be passed by two parallel sign-ins of invited friends at once.
        type(row.user).objects.select_for_update().only("pk").get(pk=row.user_id)
        locked = ReferralWelcomeBonus.objects.select_for_update().get(pk=row.pk)
        if locked.status != HELD:
            return False
        config = ReferralProgramSettings.load()
        if row.role == REFERRER and _over_monthly_limit(row.user, config):
            limit_hit = True
        else:
            limit_hit = False
            result = grant_bonus_days(user=row.user, plan=plan, days=row.days, note=note)
            row.status, row.reason = GRANTED, ""
            row.plan, row.mode, row.converted_days = plan, result.mode, result.converted_days
            row.expires_at = result.expires_at
            row.granted_at = timezone.now()
            row.save(update_fields=["status", "reason", "plan", "mode", "converted_days", "expires_at", "granted_at"])
    if limit_hit:
        _deny(row, f"Лимит: не больше {config.referrer_monthly_limit} бонусов за 30 дней")
        return False
    _notify(row)
    return True


def _deny(row: ReferralWelcomeBonus, reason: str) -> None:
    was_held = row.status == HELD
    row.status, row.reason = DENIED, reason[:255]
    row.save(update_fields=["status", "reason"])
    if was_held:
        _notify(row)


# --------------------------------------------------------------------------- page data


def summary_for(user) -> dict:
    config = ReferralProgramSettings.load()
    plan = bonus_plan(config)
    own = ReferralWelcomeBonus.objects.select_related("referral__referrer", "plan").filter(user=user, role=REFEREE).first()
    as_referrer = list(ReferralWelcomeBonus.objects.filter(user=user, role=REFERRER).values_list("referral_id", "status", "days", "reason"))
    granted = [row for row in as_referrer if row[1] == GRANTED]
    return {
        "welcome_enabled": config.welcome_bonus_enabled and plan is not None,
        "welcome_days": config.welcome_bonus_days,
        "welcome_plan_name": plan.name if plan else "",
        "welcome_own": own,
        "welcome_granted": len(granted),
        "welcome_days_earned": sum(row[2] for row in granted),
        "welcome_held": sum(1 for row in as_referrer if row[1] == HELD),
        "welcome_by_referral": {row[0]: row for row in as_referrer},
        "welcome_monthly_limit": config.referrer_monthly_limit,
    }


# --------------------------------------------------------------------------- side effects


def _notify(row: ReferralWelcomeBonus) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_REFERRAL_WELCOME_BONUS

    if row.user is None or row.referral is None:
        return
    other = row.referral.referee if row.role == REFERRER else row.referral.referrer
    try:
        notify(
            row.user,
            KIND_REFERRAL_WELCOME_BONUS,
            object_id=row.pk,
            payload={
                "role": row.role,
                "status": row.status,
                "days": row.days,
                "plan": row.plan.name if row.plan else "",
                "other": getattr(other.profile_v2, "steam_personaname", "") if hasattr(other, "profile_v2") else "",
                "other_username": other.username,
                "mode": row.mode,
                "converted_days": row.converted_days,
                "expires_at": row.expires_at.isoformat() if row.expires_at else "",
                "reason": row.reason,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Referral welcome bonus notification failed for %s", row.pk)


def _log(referral: Referral, rows: list[ReferralWelcomeBonus]) -> None:
    from .services import _log as log_activity

    labels = dict(ReferralWelcomeBonus.Status.choices)
    for row in rows:
        if row.user is None:
            continue
        who = "за приглашение " + referral.referee.username if row.role == REFERRER else "по приглашению " + referral.referrer.username
        text = f"+{row.days} дн. подписки {who}: {labels[row.status].lower()}" + (f" ({row.reason})" if row.reason else "")
        log_activity(row.user, "referral.welcome_bonus", text[:255], obj=referral)


# --------------------------------------------------------------------------- Control Center settings


class SettingsError(ValueError):
    pass


def save_settings(*, form) -> list[str]:
    """Apply the Control Center form; returns human-readable changes for the audit log."""
    from modules.v2.subscriptions.models import SubscriptionPlan

    config = ReferralProgramSettings.load()

    def number(name: str, low: int, high: int, label: str) -> int:
        try:
            value = int(str(form.get(name, "")).strip())
        except ValueError:
            raise SettingsError(f"{label}: введите число.") from None
        if not low <= value <= high:
            raise SettingsError(f"{label}: от {low} до {high}.")
        return value

    enabled = bool(form.get("welcome_bonus_enabled"))
    days = number("welcome_bonus_days", 1, 90, "Дней подписки")
    limit = number("referrer_monthly_limit", 0, 1000, "Лимит бонусов за 30 дней")
    plan_id = str(form.get("welcome_bonus_plan") or "").strip()
    plan = None
    if plan_id:
        plan = SubscriptionPlan.objects.filter(pk=plan_id if plan_id.isdigit() else 0, is_active=True).first()
        if plan is None:
            raise SettingsError("Выберите активный тариф.")

    changes = []
    if enabled != config.welcome_bonus_enabled:
        changes.append(f"бонус {'включён' if enabled else 'выключен'}")
    if days != config.welcome_bonus_days:
        changes.append(f"дней: {config.welcome_bonus_days} → {days}")
    if limit != config.referrer_monthly_limit:
        changes.append(f"лимит за 30 дней: {config.referrer_monthly_limit} → {limit}")
    if (plan.pk if plan else None) != config.welcome_bonus_plan_id:
        changes.append(f"тариф: {plan.name if plan else 'Плюс (по умолчанию)'}")
    if changes:
        config.welcome_bonus_enabled, config.welcome_bonus_days = enabled, days
        config.referrer_monthly_limit, config.welcome_bonus_plan = limit, plan
        config.save()
    return changes


def admin_totals() -> dict:
    from django.db.models import Count, Q, Sum

    since = timezone.now() - LIMIT_WINDOW
    return ReferralWelcomeBonus.objects.aggregate(
        granted=Count("id", filter=Q(status=GRANTED)),
        granted_month=Count("id", filter=Q(status=GRANTED, granted_at__gte=since)),
        days_month=Sum("days", filter=Q(status=GRANTED, granted_at__gte=since)),
        held=Count("id", filter=Q(status=HELD)),
    )
