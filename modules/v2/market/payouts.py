"""The seller's «Вывод» wallet: the market balance, what open deals still hold and the payout requests.

Every user has two balances. The game balance (UserProfile.balance_coins) takes top-ups, bonuses,
promo codes and case sales and pays for everything on the site; it never leaves the site. The market
balance (UserProfile.market_balance_coins) takes only money from completed market sales and is the
only money that can be withdrawn. A seller may also move market money to the game balance; the
other way is closed.

Payouts are manual: a request debits the market balance at once (LedgerReason.MARKET_PAYOUT) and waits
for an admin, who pays it by СБП or to a card and marks it paid, or rejects it and the amount comes
back (LedgerReason.MARKET_PAYOUT_CANCEL). A seller may cancel a request while it waits.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, Wallet
from modules.v2.profiles.services.balance import BalanceError, adjust_balance

from .models import Deal, Listing, PayoutRequest

logger = logging.getLogger(__name__)

PAYOUTS_ENABLED = True
ZERO = Decimal("0.00")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PayoutError(ValueError):
    pass


def min_payout() -> Decimal:
    return Decimal(str(getattr(settings, "MARKET_PAYOUT_MIN_RUB", "100")))


@dataclass(frozen=True)
class SellerWallet:
    earned: Decimal  # all completed sales
    withdrawn: Decimal  # paid out by admins
    pending: Decimal  # requested, waiting for an admin
    held: Decimal  # open deals: paid by buyers, not released yet
    available: Decimal  # the market balance: withdrawable now

    @property
    def can_request(self) -> bool:
        return PAYOUTS_ENABLED and self.available >= min_payout()


def is_market_seller(user) -> bool:
    if not user.is_authenticated:
        return False
    if Listing.objects.filter(seller=user).exists():
        return True
    return UserProfile.objects.filter(user=user, market_balance_coins__gt=0).exists()


def _ledger_sum(user, *reasons) -> Decimal:
    return LedgerEntry.objects.filter(user=user, reason__in=reasons).aggregate(total=Sum("delta"))["total"] or ZERO


def seller_wallet(user) -> SellerWallet:
    profile = UserProfile.objects.filter(user=user).only("market_balance_coins").first()
    held = Deal.objects.filter(seller=user, status__in=Deal.OPEN_STATUSES).aggregate(total=Sum("price"))["total"] or ZERO
    paid = PayoutRequest.objects.filter(user=user, status=PayoutRequest.Status.PAID).aggregate(total=Sum("amount"))["total"] or ZERO
    pending = PayoutRequest.objects.filter(user=user, status=PayoutRequest.Status.PENDING).aggregate(total=Sum("amount"))["total"] or ZERO
    return SellerWallet(
        earned=_ledger_sum(user, LedgerReason.MARKET_SALE),
        withdrawn=paid,
        pending=pending,
        held=held,
        available=profile.market_balance_coins if profile else ZERO,
    )


def held_deals(user):
    """Open sales with what the money waits for."""
    rows = []
    for deal in Deal.objects.filter(seller=user, status__in=Deal.OPEN_STATUSES).select_related("listing", "buyer").order_by("created_at"):
        if deal.status == Deal.Status.HOLD:
            unlock = deal.hold_until
            waits = "защита обмена Steam"
        elif deal.status == Deal.Status.DISPUTED:
            unlock, waits = None, "решение администрации"
        elif deal.status == Deal.Status.SENT:
            unlock, waits = None, "подтверждение покупателя"
        else:
            unlock, waits = None, "отправка трейда"
        rows.append({"deal": deal, "unlock": unlock, "waits": waits})
    return rows


def sales_history(user, *, limit: int = 100):
    return Deal.objects.filter(seller=user, status=Deal.Status.COMPLETED).select_related("listing", "buyer").order_by("-completed_at")[:limit]


def payout_history(user, *, limit: int = 100):
    return PayoutRequest.objects.filter(user=user).order_by("-created_at")[:limit]


# --------------------------------------------------------------------------- requests


def _parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).replace(",", ".").replace(" ", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise PayoutError("Укажите сумму числом.") from None
    if amount <= 0:
        raise PayoutError("Сумма должна быть больше нуля.")
    return amount


def _clean_requisites(method: str, raw: str) -> str:
    text = " ".join((raw or "").split())
    digits = re.sub(r"\D", "", text)
    if method == PayoutRequest.Method.CARD:
        if not 16 <= len(digits) <= 19:
            raise PayoutError("Номер карты — от 16 до 19 цифр.")
        return digits
    if method == PayoutRequest.Method.SBP:
        if len(digits) < 11:
            raise PayoutError("Для СБП укажите номер телефона и банк, например «+7 900 000-00-00, Т-Банк».")
        return text[:120]
    raise PayoutError("Выберите способ выплаты.")


def _new_code() -> str:
    while True:
        code = "PAY-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not PayoutRequest.objects.filter(code=code).exists():
            return code


@transaction.atomic
def request_payout(user, *, amount, method: str, requisites: str, recipient_name: str) -> PayoutRequest:
    if not PAYOUTS_ENABLED:
        raise PayoutError("Вывод сейчас недоступен.")
    amount = _parse_amount(amount)
    if amount < min_payout():
        raise PayoutError(f"Минимальная сумма вывода — {min_payout():.0f} ₽.")
    name = " ".join((recipient_name or "").split())[:120]
    if len(name) < 3:
        raise PayoutError("Укажите имя получателя, как в банке.")
    requisites = _clean_requisites(method, requisites)
    # Lock the seller's profile first: a row lock on a missing pending request would not stop a parallel one.
    UserProfile.objects.select_for_update().get_or_create(user=user)
    if PayoutRequest.objects.filter(user=user, status=PayoutRequest.Status.PENDING).exists():
        raise PayoutError("Предыдущая заявка ещё не обработана. Дождитесь выплаты или отмените её.")

    payout = PayoutRequest.objects.create(code=_new_code(), user=user, amount=amount, method=method, requisites=requisites, recipient_name=name)
    try:
        adjust_balance(
            user=user,
            delta=-amount,
            reason=LedgerReason.MARKET_PAYOUT,
            amount_snapshot=amount,
            withdraw_request_id=payout.pk,
            idempotency_key=f"market:{payout.code}:payout",
            comment=f"Заявка {payout.code}",
            wallet=Wallet.MARKET,
        )
    except BalanceError:
        raise PayoutError("На рыночном балансе недостаточно денег.") from None
    transaction.on_commit(lambda: _notify_admins(payout.pk))
    return payout


def _return_amount(payout: PayoutRequest, *, comment: str, created_by=None) -> None:
    adjust_balance(
        user=payout.user,
        delta=payout.amount,
        reason=LedgerReason.MARKET_PAYOUT_CANCEL,
        amount_snapshot=payout.amount,
        withdraw_request_id=payout.pk,
        idempotency_key=f"market:{payout.code}:payout_cancel",
        comment=comment[:255],
        created_by=created_by,
        wallet=Wallet.MARKET,
    )


def _locked_pending(code: str, **filters) -> PayoutRequest:
    payout = PayoutRequest.objects.select_for_update().filter(code=code, **filters).select_related("user").first()
    if payout is None:
        raise PayoutError("Заявка не найдена.")
    if payout.status != PayoutRequest.Status.PENDING:
        raise PayoutError("Заявка уже обработана.")
    return payout


@transaction.atomic
def cancel_payout(user, code: str) -> PayoutRequest:
    payout = _locked_pending(code, user=user)
    _return_amount(payout, comment=f"Заявка {payout.code} отменена")
    payout.status, payout.processed_at = PayoutRequest.Status.CANCELLED, timezone.now()
    payout.save(update_fields=["status", "processed_at"])
    return payout


@transaction.atomic
def process_payout(admin, code: str, *, paid: bool, note: str = "", request=None) -> PayoutRequest:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    payout = _locked_pending(code)
    note = (note or "").strip()[:255]
    if not paid:
        if not note:
            raise PayoutError("Укажите причину отказа — её увидит продавец.")
        _return_amount(payout, comment=f"Заявка {payout.code} отклонена: {note}", created_by=admin)
    payout.status = PayoutRequest.Status.PAID if paid else PayoutRequest.Status.REJECTED
    payout.admin_note, payout.processed_by, payout.processed_at = note, admin, timezone.now()
    payout.save(update_fields=["status", "admin_note", "processed_by", "processed_at"])
    label = payout.get_status_display()
    log_admin_action(
        actor=admin,
        action=AuditAction.MARKET_PAYOUT,
        target_user=payout.user,
        summary=f"{payout.code}: {payout.amount:.2f} ₽ — {label}"[:255],
        payload={"payout": payout.code, "amount": str(payout.amount), "paid": paid, "note": note},
        request=request,
    )
    transaction.on_commit(lambda: _notify_seller(payout.pk))
    return payout


@transaction.atomic
def transfer_to_game(user, amount) -> Decimal:
    """Moves market money to the game balance. One way only: game money never becomes withdrawable."""
    amount = _parse_amount(amount)
    key = f"market:transfer:{user.pk}:{timezone.now().timestamp():.6f}"
    try:
        adjust_balance(user=user, delta=-amount, reason=LedgerReason.MARKET_TRANSFER, amount_snapshot=amount, idempotency_key=f"{key}:out", comment="Перевод на игровой баланс", wallet=Wallet.MARKET)
    except BalanceError:
        raise PayoutError("На рыночном балансе недостаточно денег.") from None
    adjust_balance(user=user, delta=amount, reason=LedgerReason.MARKET_TRANSFER, amount_snapshot=amount, idempotency_key=f"{key}:in", comment="Перевод с рыночного баланса", wallet=Wallet.GAME)
    return amount


def pending_queue():
    return PayoutRequest.objects.filter(status=PayoutRequest.Status.PENDING).select_related("user").order_by("created_at")


# --------------------------------------------------------------------------- notices


def _notify_admins(payout_id: int) -> None:
    try:
        from modules.v2.tg_notifier.admin_events import send_to_head_admins

        payout = PayoutRequest.objects.select_related("user").get(pk=payout_id)
        send_to_head_admins(
            f"💸 Заявка на вывод {payout.code}\n"
            f"{payout.user.username}: {payout.amount:.2f} ₽ ({payout.get_method_display()})\n"
            "Control Center → Рынок → Выплаты"
        )
    except Exception:  # noqa: BLE001
        logger.warning("Payout admin notice failed for %s.", payout_id, exc_info=True)


def _notify_seller(payout_id: int) -> None:
    try:
        from modules.v2.notifications.services import notify

        from .notifications import KIND_PAYOUT

        payout = PayoutRequest.objects.select_related("user").get(pk=payout_id)
        payload = {"code": payout.code, "amount": f"{payout.amount:.2f}", "status": payout.status, "note": payout.admin_note}
        notify(payout.user, KIND_PAYOUT, object_id=payout.pk, payload=payload, dedupe=False)
    except Exception:  # noqa: BLE001
        logger.warning("Payout seller notice failed for %s.", payout_id, exc_info=True)
