"""The «Спонсор» role.

Every month starts a fresh top: on the 1st the previous month's top donors whose donations that month exceed
``sponsor_monthly_min`` become candidates for the role until the end of the current month. Anyone whose donations
ever total more than ``sponsor_lifetime_min`` becomes a candidate for a permanent role the moment it happens.
Donations themselves are kept forever, so every month's sums stay visible. Admins get a notification about new
candidates and grant (or decline) the role in Control Center; the donor is told once it is granted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import ZERO, Donation, DonationGoal, Sponsorship

logger = logging.getLogger(__name__)

MONTHS_RU = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


class SponsorError(Exception):
    pass


@dataclass
class TopRow:
    rank: int
    user: object
    total: Decimal
    qualifies: bool


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_label(period: date) -> str:
    return f"{MONTHS_RU[period.month - 1]} {period.year}"


def _aware(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def monthly_totals(period: date):
    """Per-donor totals of one calendar month (site time), biggest first."""
    start, end = _aware(period), _aware(add_months(period, 1))
    return (
        Donation.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("user_id")
        .annotate(total=Sum("amount"))
        .order_by("-total", "user_id")
    )


def monthly_top(period: date, goal: DonationGoal | None = None) -> list[TopRow]:
    goal = goal or DonationGoal.load()
    rows = list(monthly_totals(period)[: goal.sponsor_top_size])
    users = get_user_model().objects.select_related("profile_v2").in_bulk([row["user_id"] for row in rows])
    return [
        TopRow(rank=index, user=users.get(row["user_id"]), total=row["total"], qualifies=row["total"] > goal.sponsor_monthly_min)
        for index, row in enumerate(rows, start=1)
    ]


def lifetime_total(user) -> Decimal:
    return Donation.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or ZERO


def _display_name(user) -> str:
    profile = getattr(user, "profile_v2", None)
    return (getattr(profile, "steam_personaname", "") or "").strip() or user.username


def _notify_admins(payload: dict) -> None:
    from modules.v2.antifraud.services import admin_recipients
    from modules.v2.notifications.services import notify

    from .notifications import KIND_SPONSOR_CANDIDATES

    for admin in admin_recipients():
        try:
            notify(admin, KIND_SPONSOR_CANDIDATES, payload=payload, dedupe=False)
        except Exception:  # noqa: BLE001 - one admin's failed notification must not stop the others
            logger.exception("Sponsor candidate notification failed for admin %s", admin.pk)


def find_monthly_candidates(*, today: date | None = None) -> list[Sponsorship]:
    """Run on the 1st: the previous month's top donors over the monthly threshold become candidates."""
    goal = DonationGoal.load()
    if not goal.sponsors_enabled:
        return []
    period = add_months(month_start(today or timezone.localdate()), -1)
    created = []
    for row in monthly_top(period, goal):
        if not row.qualifies or row.user is None:
            continue
        sponsorship, is_new = Sponsorship.objects.get_or_create(
            user=row.user,
            kind=Sponsorship.Kind.MONTHLY,
            period=period,
            defaults={"amount": row.total, "rank": row.rank},
        )
        if is_new:
            created.append(sponsorship)
    if created:
        _notify_admins({
            "reason": "monthly",
            "month": month_label(period),
            "threshold": f"{goal.sponsor_monthly_min:.0f}",
            "names": [_display_name(item.user) for item in created],
        })
    return created


def check_lifetime(user) -> Sponsorship | None:
    """After a donation: a donor whose lifetime total just passed the threshold becomes a candidate."""
    goal = DonationGoal.load()
    if not goal.sponsors_enabled or Sponsorship.objects.filter(user=user, kind=Sponsorship.Kind.LIFETIME).exists():
        return None
    total = lifetime_total(user)
    if total <= goal.sponsor_lifetime_min:
        return None
    try:
        with transaction.atomic():
            sponsorship = Sponsorship.objects.create(user=user, kind=Sponsorship.Kind.LIFETIME, amount=total)
    except IntegrityError:  # a parallel donation already recorded it
        return None
    payload = {"reason": "lifetime", "threshold": f"{goal.sponsor_lifetime_min:.0f}", "names": [_display_name(user)]}
    transaction.on_commit(lambda: _notify_admins(payload))
    return sponsorship


def _expires_for(sponsorship: Sponsorship):
    if sponsorship.kind == Sponsorship.Kind.LIFETIME or sponsorship.period is None:
        return None
    # Earned in October, it lasts through November.
    return _aware(add_months(sponsorship.period, 2))


def grant(sponsorship: Sponsorship, *, actor) -> Sponsorship:
    if sponsorship.status not in (Sponsorship.Status.CANDIDATE, Sponsorship.Status.DECLINED, Sponsorship.Status.REVOKED):
        raise SponsorError("Роль уже выдана.")
    expires_at = _expires_for(sponsorship)
    if expires_at is not None and expires_at <= timezone.now():
        raise SponsorError("Срок этой роли уже прошёл.")
    sponsorship.status = Sponsorship.Status.GRANTED
    sponsorship.expires_at = expires_at
    sponsorship.decided_by = actor
    sponsorship.decided_at = timezone.now()
    sponsorship.save(update_fields=["status", "expires_at", "decided_by", "decided_at"])
    if DonationGoal.load().sponsor_notify_donor:
        from modules.v2.notifications.services import notify

        from .notifications import KIND_SPONSOR_GRANTED

        until = timezone.localtime(expires_at - timedelta(seconds=1)).strftime("%d.%m.%Y") if expires_at else ""
        notify(sponsorship.user, KIND_SPONSOR_GRANTED, object_id=sponsorship.pk, payload={"until": until})
    return sponsorship


def decide(sponsorship: Sponsorship, *, action: str, actor) -> Sponsorship:
    if action == "grant":
        return grant(sponsorship, actor=actor)
    if action == "decline" and sponsorship.status == Sponsorship.Status.CANDIDATE:
        sponsorship.status = Sponsorship.Status.DECLINED
    elif action == "revoke" and sponsorship.status == Sponsorship.Status.GRANTED:
        sponsorship.status = Sponsorship.Status.REVOKED
    else:
        raise SponsorError("Это действие сейчас недоступно.")
    sponsorship.decided_by = actor
    sponsorship.decided_at = timezone.now()
    sponsorship.save(update_fields=["status", "decided_by", "decided_at"])
    return sponsorship


def active_sponsorships():
    from django.db.models import Q

    return Sponsorship.objects.filter(status=Sponsorship.Status.GRANTED).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


def is_sponsor(user) -> bool:
    if not getattr(user, "pk", None):
        return False
    return active_sponsorships().filter(user=user).exists()


def sponsor_user_ids(user_ids) -> set[int]:
    return set(active_sponsorships().filter(user_id__in=list(user_ids)).values_list("user_id", flat=True))


def history(*, months: int = 6, limit: int = 20, today: date | None = None) -> dict:
    """Who donated how much, month by month (the last ``months`` months) and in total."""
    current = month_start(today or timezone.localdate())
    periods = [add_months(current, -offset) for offset in range(months)]
    lifetime = list(Donation.objects.values("user_id").annotate(total=Sum("amount")).order_by("-total", "user_id")[:limit])
    ids = [row["user_id"] for row in lifetime]
    by_month = {period: {row["user_id"]: row["total"] for row in monthly_totals(period).filter(user_id__in=ids)} for period in periods}
    users = get_user_model().objects.select_related("profile_v2").in_bulk(ids)
    sponsors = sponsor_user_ids(ids)
    rows = [
        {
            "user": users.get(row["user_id"]),
            "lifetime": row["total"],
            "months": [by_month[period].get(row["user_id"], ZERO) for period in periods],
            "is_sponsor": row["user_id"] in sponsors,
        }
        for row in lifetime
    ]
    return {"periods": [month_label(period) for period in periods], "rows": rows}
