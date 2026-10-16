"""Daily bonus: one claim per calendar day (Moscow time), a 7-day cycle, a streak that resets on a miss.

Days 1–6 give bonus units on the balance, day 7 one free opening of a case (both set in Control Center).
Only accounts with Steam linked and at least one real top-up may claim, so farming with throwaway
accounts gives nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.services.balance import adjust_balance

from .models import CYCLE_DAYS, DailyBonusClaim, DailyBonusReward, DailyBonusSettings, DailyBonusState, DailyFreeCase

ZERO = Decimal("0.00")


class DailyBonusError(Exception):
    pass


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: str = ""
    action_url: str = ""
    action_label: str = ""


def _has_real_topup(user) -> bool:
    from modules.v2.payments.models import Payment

    return Payment.objects.filter(user=user, status=Payment.Status.SUCCEEDED, is_test=False).exists()


def _is_banned(user) -> bool:
    from modules.v2.control_center.models import UserBan

    return UserBan.objects.active().filter(user=user).exists()


def eligibility(user, settings: DailyBonusSettings | None = None) -> Eligibility:
    settings = settings or DailyBonusSettings.load()
    if not settings.is_enabled:
        return Eligibility(False, "Ежедневный бонус временно отключён.")
    if _is_banned(user):
        return Eligibility(False, "Аккаунт заблокирован.")
    if settings.require_steam:
        profile = UserProfile.objects.filter(user=user).only("steam_id").first()
        if profile is None or not (profile.steam_id or "").strip():
            return Eligibility(False, "Бонус доступен аккаунтам с привязанным Steam.", reverse("profiles:profile"), "Подключить Steam")
    if settings.require_topup and not _has_real_topup(user):
        return Eligibility(False, "Бонус открывается после первого пополнения баланса.", reverse("payments:topup"), "Пополнить баланс")
    return Eligibility(True)


def rewards() -> dict[int, DailyBonusReward]:
    return {reward.day: reward for reward in DailyBonusReward.objects.select_related("case")}


def _next_day(state: DailyBonusState | None, today: date) -> tuple[int, int, bool]:
    """(cycle day to claim today, streak after that claim, whether the old streak is still alive)."""
    if state is None or state.last_claim_date is None:
        return 1, 1, False
    alive = state.last_claim_date == today - timedelta(days=1)
    if alive:
        return state.cycle_day % CYCLE_DAYS + 1, state.streak + 1, True
    return 1, 1, False


def calendar(user, *, now: datetime | None = None) -> dict:
    """Everything the bonus page shows: seven day cells, today's state and the streak."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    state = DailyBonusState.objects.filter(user=user).first()
    claimed_today = bool(state and state.last_claim_date == today)
    if claimed_today:
        progress, next_day, streak = state.cycle_day, state.cycle_day % CYCLE_DAYS + 1, state.streak
        lost = False
    else:
        next_day, _streak_after, alive = _next_day(state, today)
        progress = state.cycle_day if alive and state.cycle_day < CYCLE_DAYS else 0
        streak = state.streak if alive else 0
        lost = bool(state and state.streak and not alive)
    reward_map = rewards()
    cells = []
    for day in range(1, CYCLE_DAYS + 1):
        if day <= progress:
            status = "claimed"
        elif not claimed_today and day == next_day:
            status = "today"
        else:
            status = "upcoming"
        reward = reward_map.get(day)
        cells.append({"day": day, "status": status, "reward": reward, "is_case": bool(reward and reward.gives_case)})
    tomorrow = timezone.make_aware(datetime.combine(today + timedelta(days=1), datetime.min.time()))
    return {
        "cells": cells,
        "claimed_today": claimed_today,
        "next_day": next_day,
        "streak": streak,
        "best_streak": state.best_streak if state else 0,
        "claims_total": state.claims_total if state else 0,
        "streak_lost": lost,
        "next_claim_at": tomorrow if claimed_today else None,
    }


@transaction.atomic
def claim(user, *, ip_address: str | None = None, now: datetime | None = None) -> DailyBonusClaim:
    now = now or timezone.now()
    today = timezone.localdate(now)
    settings = DailyBonusSettings.load()
    check = eligibility(user, settings)
    if not check.ok:
        raise DailyBonusError(check.reason)

    # One lock per user serialises double clicks and parallel tabs.
    UserProfile.objects.select_for_update().get_or_create(user=user)
    state, _ = DailyBonusState.objects.get_or_create(user=user)
    if state.last_claim_date == today:
        raise DailyBonusError("Сегодняшний бонус уже получен. Следующий — завтра.")

    day, streak, _alive = _next_day(state, today)
    reward = DailyBonusReward.objects.select_related("case").filter(day=day).first()
    if reward is None:
        raise DailyBonusError("Награды ежедневного бонуса ещё не настроены.")

    entry = DailyBonusClaim.objects.create(user=user, date=today, day=day, streak=streak, kind=DailyBonusReward.Kind.BALANCE, ip_address=ip_address or None, created_at=now)
    if reward.gives_case:
        entry.kind, entry.case = DailyBonusReward.Kind.CASE, reward.case
        entry.save(update_fields=["kind", "case"])
        DailyFreeCase.objects.create(user=user, case=reward.case, claim=entry, expires_at=now + timedelta(days=settings.free_case_valid_days), created_at=now)
    elif reward.amount > 0:
        ledger_entry = adjust_balance(
            user=user,
            delta=reward.amount,
            reason=LedgerReason.DAILY_BONUS,
            amount_snapshot=reward.amount,
            idempotency_key=f"daily_bonus:{user.pk}:{today.isoformat()}",
            comment=f"Ежедневный бонус, день {day}",
        )
        entry.amount, entry.ledger_entry_id = reward.amount, ledger_entry.pk
        entry.save(update_fields=["amount", "ledger_entry_id"])

    state.cycle_day = day
    state.streak = streak
    state.best_streak = max(state.best_streak, streak)
    state.last_claim_date = today
    state.claims_total += 1
    state.save()
    _resolve_reminder(user)
    from modules.v2.webpush.services import prompt_after_commit

    prompt_after_commit(user, "daily_bonus")
    return entry


def _resolve_reminder(user) -> None:
    from modules.v2.notifications.models import Notification

    from .notifications import KIND_REMINDER

    Notification.objects.filter(recipient=user, kind=KIND_REMINDER, is_read=False).update(is_read=True, read_at=timezone.now())


# --------------------------------------------------------------------------- free case


def free_case_for(user, case) -> DailyFreeCase | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return DailyFreeCase.objects.filter(user=user, case=case, status=DailyFreeCase.Status.PENDING, expires_at__gt=timezone.now()).order_by("expires_at").first()


def consume_free_case(*, user, case) -> DailyFreeCase | None:
    """Use up a won free opening of this case (inside the opening transaction)."""
    free = (
        DailyFreeCase.objects.select_for_update()
        .filter(user=user, case=case, status=DailyFreeCase.Status.PENDING, expires_at__gt=timezone.now())
        .order_by("expires_at", "id")
        .first()
    )
    if free is None:
        return None
    free.status, free.used_at = DailyFreeCase.Status.USED, timezone.now()
    free.save(update_fields=["status", "used_at"])
    return free


def pending_free_cases(user) -> list[DailyFreeCase]:
    return list(DailyFreeCase.objects.select_related("case").filter(user=user, status=DailyFreeCase.Status.PENDING, expires_at__gt=timezone.now()))


# --------------------------------------------------------------------------- reminders


def remind_streaks(*, now: datetime | None = None) -> int:
    """Evening nudge to everyone whose streak breaks at midnight unless they claim today."""
    from modules.v2.notifications.services import notify

    from .notifications import KIND_REMINDER

    if not DailyBonusSettings.load().is_enabled:
        return 0
    today = timezone.localdate(now or timezone.now())
    sent = 0
    for state in DailyBonusState.objects.select_related("user").filter(last_claim_date=today - timedelta(days=1)):
        next_day = state.cycle_day % CYCLE_DAYS + 1
        if notify(state.user, KIND_REMINDER, payload={"day": next_day, "streak": state.streak, "date": today.isoformat()}) is not None:
            sent += 1
    return sent


# --------------------------------------------------------------------------- Control Center

MAX_REWARD = Decimal("1000.00")


def save_config(*, form: dict) -> list[str]:
    """Apply the Control Center form (plain POST data). Returns human-readable changes; raises DailyBonusError."""
    from modules.v2.case_simulator.models import SimCase

    settings = DailyBonusSettings.load()
    changes: list[str] = []
    for field, label in (("is_enabled", "включён"), ("require_steam", "нужен Steam"), ("require_topup", "нужно пополнение")):
        value = form.get(field) in ("1", "on", "true")
        if getattr(settings, field) != value:
            changes.append(f"{label}: {'да' if value else 'нет'}")
            setattr(settings, field, value)
    try:
        valid_days = int(form.get("free_case_valid_days") or settings.free_case_valid_days)
    except (TypeError, ValueError) as exc:
        raise DailyBonusError("Срок бесплатного кейса — целое число дней.") from exc
    if not 1 <= valid_days <= 60:
        raise DailyBonusError("Срок бесплатного кейса — от 1 до 60 дней.")
    if valid_days != settings.free_case_valid_days:
        changes.append(f"срок кейса: {valid_days} дн.")
        settings.free_case_valid_days = valid_days

    cases = {case.pk: case for case in SimCase.objects.filter(is_active=True).exclude(kind=SimCase.Kind.TERMINAL)}
    updated = []
    for day in range(1, CYCLE_DAYS + 1):
        reward, _ = DailyBonusReward.objects.get_or_create(day=day)
        kind = form.get(f"kind_{day}") or reward.kind
        if kind not in DailyBonusReward.Kind.values:
            raise DailyBonusError(f"День {day}: неизвестный тип награды.")
        try:
            amount = Decimal(str(form.get(f"amount_{day}") or "0").replace(",", ".")).quantize(Decimal("0.01"))
        except Exception as exc:  # noqa: BLE001 - any parse error is a form error
            raise DailyBonusError(f"День {day}: сумма должна быть числом.") from exc
        if not ZERO <= amount <= MAX_REWARD:
            raise DailyBonusError(f"День {day}: сумма от 0 до {MAX_REWARD:.0f} ₽.")
        case = None
        if kind == DailyBonusReward.Kind.CASE:
            raw_case = str(form.get(f"case_{day}") or "")
            case = cases.get(int(raw_case)) if raw_case.isdigit() else None
            if case is None:
                raise DailyBonusError(f"День {day}: выберите кейс для награды.")
        if (reward.kind, reward.amount, reward.case_id) != (kind, amount, getattr(case, "pk", None)):
            reward.kind, reward.amount, reward.case = kind, amount, case
            updated.append(reward)
            changes.append(f"день {day}: {reward.label}")
    with transaction.atomic():
        settings.save()
        for reward in updated:
            reward.save()
    return changes


def admin_summary(*, now: datetime | None = None) -> dict:
    from modules.v2.profiles.models import LedgerEntry

    now = now or timezone.now()
    today = timezone.localdate(now)
    week_ago, month_ago = today - timedelta(days=6), today - timedelta(days=29)
    week = DailyBonusClaim.objects.filter(date__gte=week_ago)
    return {
        "claims_today": DailyBonusClaim.objects.filter(date=today).count(),
        "claims_week": week.count(),
        "users_week": week.values("user_id").distinct().count(),
        "paid_month": LedgerEntry.objects.filter(reason=LedgerReason.DAILY_BONUS, created_at__gte=now - timedelta(days=30)).aggregate(total=Sum("delta"))["total"] or ZERO,
        "cases_month": DailyFreeCase.objects.filter(created_at__gte=now - timedelta(days=30)).aggregate(
            issued=Count("id"), used=Count("id", filter=Q(status=DailyFreeCase.Status.USED))
        ),
        "active_streaks": DailyBonusState.objects.filter(last_claim_date__gte=today - timedelta(days=1)).count(),
        "by_day": {row["day"]: row["total"] for row in DailyBonusClaim.objects.filter(date__gte=month_ago).values("day").annotate(total=Count("id"))},
        "top_streaks": list(DailyBonusState.objects.select_related("user", "user__profile_v2").filter(streak__gt=0, last_claim_date__gte=today - timedelta(days=1)).order_by("-streak", "user_id")[:10]),
        "recent": list(DailyBonusClaim.objects.select_related("user", "user__profile_v2", "case").order_by("-created_at", "-id")[:30]),
    }
