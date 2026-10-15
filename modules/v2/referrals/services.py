"""Referral program.

* Every user has an invite link /r/<code>/. Opening it stores the code in a cookie; a *new* account
  created by the next Steam sign-in is bound to the referrer (accounts.social_pipeline step).
* An invited user "counts" once their real top-ups reach REFERRAL_QUALIFY_TOPUP_RUB and the referral
  is not under review. Accounts that share an IP with the referrer go to review (FLAGGED) and count
  only after an admin marks them clean.
* Rewards for the referrer depend on the number of counted referrals N:
    - subscription discount: N %, up to REFERRAL_DISCOUNT_MAX_PERCENT (50 %);
    - after that, a turnover bonus: (N − 50) % of every case-simulator item sale of their counted
      referrals, up to REFERRAL_TURNOVER_MAX_PERCENT (10 %), credited to the balance.
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import Referral, ReferralBonus, ReferralCode

logger = logging.getLogger(__name__)

COOKIE_NAME = "caseup_ref"
SESSION_KEY = "caseup_ref"
IP_LOOKBACK = timedelta(days=30)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
MONEY_Q = Decimal("0.01")
HUNDRED = Decimal("100")


# --------------------------------------------------------------------------- settings


def qualify_amount() -> Decimal:
    return Decimal(str(getattr(settings, "REFERRAL_QUALIFY_TOPUP_RUB", "100"))).quantize(MONEY_Q)


def discount_cap() -> int:
    return int(getattr(settings, "REFERRAL_DISCOUNT_MAX_PERCENT", 50))


def turnover_cap() -> int:
    return int(getattr(settings, "REFERRAL_TURNOVER_MAX_PERCENT", 10))


def cookie_days() -> int:
    return int(getattr(settings, "REFERRAL_COOKIE_DAYS", 30))


# --------------------------------------------------------------------------- codes and links


def normalize_code(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())[:16]


def get_or_create_code(user) -> ReferralCode:
    existing = ReferralCode.objects.filter(user=user).first()
    if existing is not None:
        return existing
    for _ in range(10):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(user=user, code=code)
        except IntegrityError:
            existing = ReferralCode.objects.filter(user=user).first()
            if existing is not None:
                return existing
    raise RuntimeError("Could not generate a unique referral code")


def find_code(raw: str) -> ReferralCode | None:
    code = normalize_code(raw)
    if not code:
        return None
    return ReferralCode.objects.select_related("user").filter(code=code).first()


# --------------------------------------------------------------------------- tiers


def counted_referrals(user):
    return Referral.objects.filter(referrer=user, qualified_at__isnull=False, review=Referral.Review.OK)


def counted_count(user) -> int:
    if not getattr(user, "is_authenticated", False):
        return 0
    return counted_referrals(user).count()


def discount_percent_for(count: int) -> int:
    return max(0, min(count, discount_cap()))


def turnover_percent_for(count: int) -> int:
    return max(0, min(count - discount_cap(), turnover_cap()))


def subscription_discount_percent(user) -> int:
    return discount_percent_for(counted_count(user))


def apply_discount(amount: Decimal, percent: int | Decimal) -> Decimal:
    amount = Decimal(amount or 0)
    if not percent:
        return amount.quantize(MONEY_Q)
    return (amount * (HUNDRED - Decimal(percent)) / HUNDRED).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- shared IPs


def _public(ip: str | None) -> bool:
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified)


def recent_ips(user, *, extra: tuple[str | None, ...] = ()) -> set[str]:
    from modules.v2.accounts.models import UserSession

    since = timezone.now() - IP_LOOKBACK
    rows = UserSession.objects.filter(user=user).filter(Q(created_at__gte=since) | Q(last_seen_at__gte=since)).values_list("ip_address", "last_ip")
    ips = {ip for pair in rows for ip in pair if ip}
    ips.update(ip for ip in extra if ip)
    return {ip for ip in ips if _public(ip)}


def shared_ips(referral: Referral) -> set[str]:
    return recent_ips(referral.referrer) & recent_ips(referral.referee, extra=(referral.ip_address,))


def _flag_if_shared_ip(referral: Referral) -> bool:
    """Put a referral on review when the two accounts were seen on the same IP. Admin decisions stick."""
    if referral.review != Referral.Review.OK or referral.reviewed_at is not None:
        return False
    common = shared_ips(referral)
    if not common:
        return False
    referral.review = Referral.Review.FLAGGED
    referral.flag_reason = "Общий IP с пригласившим: " + ", ".join(sorted(common)[:3])
    return True


# --------------------------------------------------------------------------- binding


def bind_new_user(*, user, code: str, ip_address: str | None = None) -> Referral | None:
    """Attach a freshly registered account to the owner of ``code``. Never raises."""
    ref_code = find_code(code)
    if ref_code is None:
        return None
    return bind_to_referrer(user=user, referrer=ref_code.user, code=ref_code.code, ip_address=ip_address)


def bind_to_referrer(*, user, referrer, code: str = "", ip_address: str | None = None, source: str = "ссылке") -> Referral | None:
    """Make ``user`` the referral of ``referrer`` unless they already have one. Never raises."""
    try:
        if referrer is None or referrer.pk == user.pk or not referrer.is_active:
            return None
        if Referral.objects.filter(referee=user).exists():
            return None
        referral = Referral(referrer=referrer, referee=user, code=code, ip_address=ip_address or None)
        _flag_if_shared_ip(referral)
        with transaction.atomic():
            referral.save()
        _log(user, "referral.bound", f"Пришёл по {source} {referrer.username}", obj=referral)
        if referral.review == Referral.Review.FLAGGED:
            _log(referrer, "referral.flagged", f"Реферал {user.username} на проверке: {referral.flag_reason}", obj=referral)
        return referral
    except Exception:  # noqa: BLE001 - a sign-up must never fail because of the referral program
        logger.exception("Could not bind user %s to referrer %s", getattr(user, "pk", None), getattr(referrer, "pk", None))
        return None


# --------------------------------------------------------------------------- qualification


def real_topup_total(user) -> Decimal:
    from modules.v2.payments.models import Payment

    payments = Payment.objects.filter(user=user, status=Payment.Status.SUCCEEDED)
    if not getattr(settings, "REFERRAL_COUNT_TEST_PAYMENTS", False):
        payments = payments.filter(is_test=False)
    return Decimal(payments.aggregate(total=Sum("amount"))["total"] or 0).quantize(MONEY_Q)


def check_qualification(user) -> Referral | None:
    """Called after every top-up: mark the referral qualified once the threshold is reached."""
    with transaction.atomic():
        referral = Referral.objects.select_for_update(of=("self",)).select_related("referrer", "referee").filter(referee=user, qualified_at__isnull=True).first()
        if referral is None or real_topup_total(user) < qualify_amount():
            return None
        referral.qualified_at = timezone.now()
        _flag_if_shared_ip(referral)
        referral.save(update_fields=["qualified_at", "review", "flag_reason"])

    _log(referral.referrer, "referral.qualified", f"Реферал {referral.referee.username} пополнил баланс и засчитан" if referral.counts else f"Реферал {referral.referee.username} пополнил баланс, но на проверке: {referral.flag_reason}", obj=referral)
    if referral.counts:
        _notify_counted(referral)
    return referral


# --------------------------------------------------------------------------- turnover bonus


def award_turnover_bonus(entry) -> ReferralBonus | None:
    """Pay the referrer their share of one case-simulator item sale (idempotent per sale)."""
    from modules.v2.profiles.models import LedgerReason
    from modules.v2.profiles.services.balance import adjust_balance

    if entry.reason != LedgerReason.ITEM_SALE or entry.case_opening_id is None or entry.delta <= 0:
        return None
    referral = Referral.objects.select_related("referrer", "referee").filter(referee_id=entry.user_id).first()
    if referral is None or not referral.counts or entry.created_at < referral.qualified_at:
        return None
    percent = turnover_percent_for(counted_count(referral.referrer))
    if percent <= 0:
        return None
    amount = (Decimal(entry.delta) * Decimal(percent) / HUNDRED).quantize(MONEY_Q, rounding=ROUND_DOWN)
    if amount < MONEY_Q:
        return None

    with transaction.atomic():
        bonus, created = ReferralBonus.objects.get_or_create(
            source_entry_id=entry.pk,
            defaults={"referral": referral, "sale_amount": entry.delta, "percent": Decimal(percent), "amount": amount},
        )
        if not created:
            return bonus
        ledger_entry = adjust_balance(
            user=referral.referrer,
            delta=amount,
            reason=LedgerReason.REFERRAL_BONUS,
            amount_snapshot=entry.delta,
            idempotency_key=f"referral_bonus:{entry.pk}",
            comment=f"{percent}% с продажи реферала {referral.referee.username}",
        )
        bonus.ledger_entry = ledger_entry
        bonus.save(update_fields=["ledger_entry"])
        Referral.objects.filter(pk=referral.pk).update(bonus_total=F("bonus_total") + amount)
    return bonus


# --------------------------------------------------------------------------- admin review


class ReferralReviewError(ValueError):
    pass


def review_referral(*, actor, referral: Referral, decision: str, note: str = "", request=None) -> Referral:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    if decision not in (Referral.Review.OK, Referral.Review.BLOCKED):
        raise ReferralReviewError("Неизвестное решение.")
    was_counted = referral.counts
    referral.review = decision
    referral.reviewed_by = actor
    referral.reviewed_at = timezone.now()
    referral.review_note = (note or "").strip()[:255]
    referral.save(update_fields=["review", "reviewed_by", "reviewed_at", "review_note"])
    label = "засчитан" if decision == Referral.Review.OK else "отклонён"
    log_admin_action(
        actor=actor,
        action=AuditAction.REFERRAL_REVIEW,
        target_user=referral.referrer,
        summary=f"Реферал {referral.referee.username} → {referral.referrer.username}: {label}",
        payload={"referral_id": referral.pk, "decision": decision, "note": referral.review_note},
        request=request,
    )
    if referral.counts and not was_counted:
        _notify_counted(referral)
    return referral


# --------------------------------------------------------------------------- reconcile


def reconcile(*, now=None, sales_window: timedelta = timedelta(days=3)) -> dict[str, int]:
    """Safety net for the on-commit hooks (a crash between the sale and the bonus): re-check pending
    referrals with successful top-ups and recent item sales without a bonus."""
    from modules.v2.payments.models import Payment
    from modules.v2.profiles.models import LedgerEntry, LedgerReason

    now = now or timezone.now()
    qualified = 0
    pending_ids = (
        Referral.objects.filter(qualified_at__isnull=True, referee__payments__status=Payment.Status.SUCCEEDED)
        .values_list("referee_id", flat=True)
        .distinct()
    )
    from django.contrib.auth import get_user_model

    for user in get_user_model().objects.filter(pk__in=list(pending_ids)):
        qualified += bool(check_qualification(user))

    awarded = 0
    referee_ids = Referral.objects.filter(qualified_at__isnull=False, review=Referral.Review.OK).values_list("referee_id", flat=True)
    sales = (
        LedgerEntry.objects.filter(
            reason=LedgerReason.ITEM_SALE,
            case_opening_id__isnull=False,
            created_at__gte=now - sales_window,
            user_id__in=referee_ids,
        )
        .exclude(pk__in=ReferralBonus.objects.values("source_entry_id"))
        .order_by("id")
    )
    for entry in sales.iterator():
        awarded += bool(award_turnover_bonus(entry))
    return {"qualified": qualified, "bonuses": awarded}


# --------------------------------------------------------------------------- summary for the page


def build_summary(user) -> dict:
    referrals = list(
        Referral.objects.filter(referrer=user)
        .select_related("referee", "referee__profile_v2")
        .order_by("-created_at", "-id")
    )
    counted = sum(1 for item in referrals if item.counts)
    discount = discount_percent_for(counted)
    turnover = turnover_percent_for(counted)
    cap = discount_cap()
    earned = sum((item.bonus_total for item in referrals), Decimal("0.00"))
    return {
        "referrals": referrals,
        "invited": len(referrals),
        "counted": counted,
        "waiting": sum(1 for item in referrals if not item.is_qualified and item.review != Referral.Review.BLOCKED),
        "on_review": sum(1 for item in referrals if item.review == Referral.Review.FLAGGED),
        "discount": discount,
        "discount_cap": cap,
        "discount_progress": int(discount * 100 / cap) if cap else 0,
        "turnover": turnover,
        "turnover_cap": turnover_cap(),
        "turnover_progress": int(turnover * 100 / turnover_cap()) if turnover_cap() else 0,
        "to_turnover": max(0, cap + 1 - counted),
        "earned": earned,
        "qualify_amount": qualify_amount(),
    }


# --------------------------------------------------------------------------- side effects


def _log(user, event: str, summary: str, *, obj=None) -> None:
    from modules.v2.control_center.models import ActivityCategory
    from modules.v2.control_center.services.activity import log_activity

    log_activity(category=ActivityCategory.ACCOUNT, event=event, summary=summary, user=user, obj=obj)


def _notify_counted(referral: Referral) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_REFERRAL_COUNTED

    count = counted_count(referral.referrer)
    try:
        notify(
            referral.referrer,
            KIND_REFERRAL_COUNTED,
            object_id=referral.pk,
            dedupe=False,
            payload={
                "referee": referral.referee.username,
                "count": count,
                "discount": discount_percent_for(count),
                "turnover": turnover_percent_for(count),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Referral notification failed for referral %s", referral.pk)
