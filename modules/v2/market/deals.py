"""Market, stage 2: deals with the site as the guarantor.

Life of a deal (MKT-XXXXXX):

1. **paid** — the buyer pays from the balance; the money is frozen (taken off the balance, held by the
   deal) and the lot leaves the showcase. The seller has ``MARKET_SEND_HOURS`` to send the Steam trade.
2. **sent** — the seller pressed «Трейд отправлен».
3. **hold** — the buyer pressed «Трейд принят», or the site itself saw the item arrive in the buyer's
   inventory. Steam's trade protection lasts 7 days (a trade can be reversed in that time), so the money
   stays held for ``MARKET_HOLD_DAYS``.
4. **completed** — the hold passed without a dispute: the seller gets the money.

Before the trade is sent the seller may cancel, and the buyer may cancel once the send deadline passed;
both refund the buyer in full. At any open stage either side may call the administration (**disputed**);
an admin then refunds the buyer or pays the seller. Every step goes to the market journal.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.services.balance import BalanceError, adjust_balance

from .models import Deal, Listing, MarketEvent, MarketSeller
from .services import MarketError, _steam_id64, log_event

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class InsufficientFunds(MarketError):
    def __init__(self, shortfall: Decimal):
        self.shortfall = shortfall
        super().__init__(f"Не хватает {shortfall:.2f} ₽ на балансе.")


def _setting(name: str, default):
    return getattr(settings, name, default)


def _new_code() -> str:
    while True:
        code = "MKT-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not Deal.objects.filter(code=code).exists():
            return code


def _notify(user, event: str, deal: Deal, **extra) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_DEAL

    try:
        notify(user, KIND_DEAL, object_id=deal.pk, payload={"event": event, "code": deal.code, "name": deal.listing.market_hash_name, "price": f"{deal.price:.2f}", **extra}, dedupe=False)
    except Exception:  # noqa: BLE001 - a notification must never undo a money operation
        logger.exception("Market notification %s for %s failed", event, deal.code)


def _notify_admins(deal: Deal) -> None:
    from django.contrib.auth import get_user_model

    from modules.v2.profiles.models import UserRole

    for admin in get_user_model().objects.filter(profile_v2__role__in=[UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN]):
        _notify(admin, "dispute_admin", deal)


# --------------------------------------------------------------------------- buying


def buyer_problems(user) -> list[str]:
    """What stops the user from buying (empty when they can)."""
    from modules.v2.control_center.models import UserBan

    problems = []
    if not _steam_id64(user):
        problems.append("Привяжите Steam к аккаунту — предмет придёт обменом в Steam.")
    seller = MarketSeller.objects.filter(user=user).first()
    if not seller or not seller.trade_url:
        problems.append("Сохраните свою трейд-ссылку Steam — по ней продавец отправит предмет.")
    if UserBan.objects.active().filter(user=user).exists():
        problems.append("Аккаунт заблокирован.")
    return problems


def shortfall_for(user, listing: Listing) -> Decimal:
    profile = UserProfile.objects.filter(user=user).only("balance_coins").first()
    balance = profile.balance_coins if profile else Decimal("0.00")
    return max(listing.price - balance, Decimal("0.00"))


def buy(buyer, *, listing_id, ip: str | None = None) -> Deal:
    from modules.v2.social_friends.services import is_blocked

    problems = buyer_problems(buyer)
    if problems:
        raise MarketError(problems[0])
    with transaction.atomic():
        listing = Listing.objects.select_for_update().select_related("seller").filter(pk=listing_id).first()
        if listing is None or listing.status != Listing.Status.ACTIVE:
            raise MarketError("Этот лот уже купили или сняли с продажи.")
        if listing.seller_id == buyer.pk:
            raise MarketError("Нельзя купить собственный лот.")
        if is_blocked(buyer, listing.seller) or is_blocked(listing.seller, buyer):
            raise MarketError("Сделка невозможна: один из вас заблокировал другого.")
        shortfall = shortfall_for(buyer, listing)
        if shortfall > 0:
            raise InsufficientFunds(shortfall)

        now = timezone.now()
        seller_profile = MarketSeller.objects.filter(user=listing.seller).first()
        deal = Deal.objects.create(
            code=_new_code(),
            listing=listing,
            buyer=buyer,
            seller=listing.seller,
            price=listing.price,
            buyer_trade_url=MarketSeller.objects.get(user=buyer).trade_url,
            seller_trade_url=seller_profile.trade_url if seller_profile else "",
            created_at=now,
            send_deadline=now + timedelta(hours=int(_setting("MARKET_SEND_HOURS", 24))),
        )
        try:
            adjust_balance(user=buyer, delta=-deal.price, reason=LedgerReason.MARKET_PURCHASE, amount_snapshot=deal.price, idempotency_key=f"market:{deal.code}:purchase", comment=f"Сделка {deal.code}: {listing.market_hash_name}")
        except BalanceError as exc:
            raise InsufficientFunds(shortfall_for(buyer, listing) or deal.price) from exc
        listing.status = Listing.Status.RESERVED
        listing.save(update_fields=["status", "updated_at"])
        log_event(MarketEvent.Kind.DEAL_CREATED, user=buyer, deal=deal, message=f"{deal.code}: куплен {listing.code} за {deal.price:.2f} ₽, деньги заморожены", payload={"price": str(deal.price)}, ip=ip)
        transaction.on_commit(lambda: _after_purchase(deal.pk))
    return deal


def _after_purchase(deal_id: int) -> None:
    deal = Deal.objects.select_related("listing", "buyer", "seller").get(pk=deal_id)
    _open_chat(deal)
    _notify(deal.seller, "new", deal, deadline=timezone.localtime(deal.send_deadline).strftime("%d.%m %H:%M"))


def _open_chat(deal: Deal) -> None:
    """The deal chat is the regular direct chat of the two users; it stays after the deal."""
    from django.urls import reverse

    from modules.v2.social.services import create_space_message, get_or_create_direct_space

    try:
        space = get_or_create_direct_space(deal.buyer, deal.seller, ignore_privacy=True)
        Deal.objects.filter(pk=deal.pk).update(chat_space_id=space.pk)
        create_space_message(
            deal.buyer,
            space,
            f"🛒 Сделка {deal.code}: покупаю «{deal.listing.market_hash_name}» за {deal.price:.2f} ₽. "
            f"Деньги заморожены сайтом. Отправьте, пожалуйста, обмен в Steam: {reverse('market:deal', args=[deal.code])}",
        )
    except Exception:  # noqa: BLE001 - the deal works without a chat; the page shows the trade link anyway
        logger.exception("Could not open the chat for %s", deal.code)


def _locked(code: str) -> Deal:
    deal = Deal.objects.select_for_update().select_related("listing").filter(code=code).first()
    if deal is None:
        raise MarketError("Сделка не найдена.")
    return deal


# --------------------------------------------------------------------------- the trade


@transaction.atomic
def mark_sent(seller, code: str, *, ip: str | None = None) -> Deal:
    deal = _locked(code)
    if deal.seller_id != seller.pk:
        raise MarketError("Отправить трейд может только продавец.")
    if deal.status != Deal.Status.PAID:
        raise MarketError("Трейд уже отмечен или сделка закрыта.")
    deal.status, deal.sent_at = Deal.Status.SENT, timezone.now()
    deal.save(update_fields=["status", "sent_at"])
    log_event(MarketEvent.Kind.DEAL_SENT, user=seller, deal=deal, message=f"{deal.code}: продавец отправил трейд", ip=ip)
    transaction.on_commit(lambda: _notify(deal.buyer, "sent", deal))
    return deal


def _start_hold(deal: Deal, *, now) -> None:
    deal.status = Deal.Status.HOLD
    deal.hold_until = now + timedelta(days=int(_setting("MARKET_HOLD_DAYS", 7)))


@transaction.atomic
def confirm_received(buyer, code: str, *, ip: str | None = None) -> Deal:
    deal = _locked(code)
    if deal.buyer_id != buyer.pk:
        raise MarketError("Подтвердить получение может только покупатель.")
    if deal.status not in (Deal.Status.PAID, Deal.Status.SENT):
        raise MarketError("Сделка уже подтверждена или закрыта.")
    now = timezone.now()
    deal.accepted_at = now
    deal.sent_at = deal.sent_at or now
    _start_hold(deal, now=now)
    deal.save(update_fields=["status", "accepted_at", "sent_at", "hold_until"])
    log_event(MarketEvent.Kind.DEAL_ACCEPTED, user=buyer, deal=deal, message=f"{deal.code}: покупатель принял трейд, удержание до {timezone.localtime(deal.hold_until):%d.%m %H:%M}", ip=ip)
    transaction.on_commit(lambda: _notify(deal.seller, "accepted", deal, until=timezone.localtime(deal.hold_until).strftime("%d.%m %H:%M")))
    return deal


def _refund(deal: Deal, *, reason: str) -> None:
    adjust_balance(user=deal.buyer, delta=deal.price, reason=LedgerReason.MARKET_REFUND, amount_snapshot=deal.price, idempotency_key=f"market:{deal.code}:refund", comment=f"Сделка {deal.code}: {reason}"[:255])
    deal.status, deal.cancelled_at, deal.cancel_reason = Deal.Status.CANCELLED, timezone.now(), reason[:255]
    listing = Listing.objects.select_for_update().get(pk=deal.listing_id)
    listing.status, listing.closed_at = Listing.Status.CANCELLED, timezone.now()
    listing.save(update_fields=["status", "closed_at", "updated_at"])


@transaction.atomic
def cancel(user, code: str, *, ip: str | None = None) -> Deal:
    deal = _locked(code)
    party = deal.party(user)
    if deal.status != Deal.Status.PAID:
        raise MarketError("После отправки трейда сделку нельзя отменить — вызовите администрацию.")
    if party == "seller":
        reason = "продавец отменил сделку"
    elif party == "buyer":
        if timezone.now() < deal.send_deadline:
            raise MarketError(f"Покупатель может отменить сделку, если продавец не отправил трейд до {timezone.localtime(deal.send_deadline):%d.%m %H:%M}.")
        reason = "продавец не отправил трейд вовремя, покупатель отменил сделку"
    else:
        raise MarketError("Это не ваша сделка.")
    _refund(deal, reason=reason)
    deal.save(update_fields=["status", "cancelled_at", "cancel_reason"])
    log_event(MarketEvent.Kind.DEAL_CANCELLED, user=user, deal=deal, message=f"{deal.code}: {reason}, {deal.price:.2f} ₽ возвращены покупателю", payload={"price": str(deal.price)}, ip=ip)
    other = deal.seller if party == "buyer" else deal.buyer
    transaction.on_commit(lambda: _notify(other, "cancelled", deal, reason=reason))
    return deal


@transaction.atomic
def open_dispute(user, code: str, *, reason: str, ip: str | None = None) -> Deal:
    deal = _locked(code)
    party = deal.party(user)
    if not party:
        raise MarketError("Это не ваша сделка.")
    if deal.status not in (Deal.Status.PAID, Deal.Status.SENT, Deal.Status.HOLD):
        raise MarketError("По этой сделке спор открыть нельзя.")
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise MarketError("Опишите проблему подробнее (хотя бы пару предложений).")
    deal.status_before_dispute, deal.status = deal.status, Deal.Status.DISPUTED
    deal.disputed_at, deal.disputed_by, deal.dispute_reason = timezone.now(), user, reason[:2000]
    deal.save(update_fields=["status", "status_before_dispute", "disputed_at", "disputed_by", "dispute_reason"])
    log_event(MarketEvent.Kind.DEAL_DISPUTED, user=user, deal=deal, message=f"{deal.code}: {'покупатель' if party == 'buyer' else 'продавец'} вызвал администрацию", payload={"reason": reason[:500]}, ip=ip)
    other = deal.seller if party == "buyer" else deal.buyer
    transaction.on_commit(lambda: (_notify(other, "disputed", deal), _notify_admins(deal)))
    return deal


# --------------------------------------------------------------------------- closing


def _complete(deal: Deal, *, by_admin=None) -> None:
    adjust_balance(user=deal.seller, delta=deal.price, reason=LedgerReason.MARKET_SALE, amount_snapshot=deal.price, idempotency_key=f"market:{deal.code}:sale", comment=f"Сделка {deal.code}: {deal.listing.market_hash_name}"[:255])
    deal.status, deal.completed_at = Deal.Status.COMPLETED, timezone.now()
    listing = Listing.objects.select_for_update().get(pk=deal.listing_id)
    listing.status, listing.closed_at = Listing.Status.SOLD, timezone.now()
    listing.save(update_fields=["status", "closed_at", "updated_at"])


@transaction.atomic
def resolve_dispute(admin, code: str, *, decision: str, note: str = "", request=None) -> Deal:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    deal = _locked(code)
    if deal.status != Deal.Status.DISPUTED:
        raise MarketError("Решать можно только открытый спор.")
    note = (note or "").strip()
    if decision == Deal.Resolution.REFUND:
        _refund(deal, reason=f"решение администрации: {note or 'возврат покупателю'}")
    elif decision == Deal.Resolution.PAY_SELLER:
        _complete(deal, by_admin=admin)
    else:
        raise MarketError("Выберите решение.")
    deal.resolution, deal.resolution_note, deal.resolved_by, deal.resolved_at = decision, note[:500], admin, timezone.now()
    deal.save()
    label = Deal.Resolution(decision).label
    log_event(MarketEvent.Kind.DEAL_RESOLVED, user=deal.buyer, actor=admin, deal=deal, message=f"{deal.code}: {label}" + (f" — {note}" if note else ""), payload={"decision": decision, "price": str(deal.price)})
    log_admin_action(actor=admin, action=AuditAction.MARKET_DISPUTE, target_user=deal.buyer, summary=f"{deal.code}: {label}"[:255], payload={"deal": deal.code, "decision": decision, "note": note[:500]}, request=request)
    transaction.on_commit(lambda: (_notify(deal.buyer, "resolved", deal, decision=label), _notify(deal.seller, "resolved", deal, decision=label)))
    return deal


# --------------------------------------------------------------------------- background checks


def _buyer_has_item(deal: Deal) -> bool | None:
    """True/False when the buyer's inventory could be read, None when it could not."""
    from modules.v2.farm.services import fetch_public_inventory

    steam_id = _steam_id64(deal.buyer)
    if not steam_id:
        return None
    try:
        items = fetch_public_inventory(steam_id)
    except Exception:  # noqa: BLE001 - a closed or unreachable inventory proves nothing either way
        return None
    target_float = float(deal.listing.float_value) if deal.listing.float_value is not None else None
    for item in items:
        if item.get("market_hash_name") != deal.listing.market_hash_name:
            continue
        if target_float is None or item.get("float_value") is None or abs(float(item["float_value"]) - target_float) < 1e-6:
            return True
    return False


def process_deals(*, now=None, inventory_checks: int | None = None) -> dict:
    """Celery: deadline reminders, automatic proof of the trade, and completion after the hold."""
    now = now or timezone.now()
    report = {"deadline": 0, "proof": 0, "completed": 0, "auto_disputed": 0}
    checks_left = int(_setting("MARKET_PROOF_CHECKS_PER_RUN", 20)) if inventory_checks is None else inventory_checks

    for deal in Deal.objects.select_related("listing", "buyer", "seller").filter(status=Deal.Status.PAID, send_deadline__lte=now, deadline_notified_at__isnull=True):
        Deal.objects.filter(pk=deal.pk).update(deadline_notified_at=now)
        _notify(deal.buyer, "deadline", deal)
        report["deadline"] += 1

    # The seller says the trade is sent but the buyer is silent: look at the buyer's inventory ourselves.
    recheck_after = now - timedelta(minutes=int(_setting("MARKET_PROOF_RECHECK_MINUTES", 60)))
    for deal in Deal.objects.select_related("listing", "buyer").filter(status=Deal.Status.SENT, proof_at__isnull=True).exclude(proof_checked_at__gt=recheck_after).order_by("proof_checked_at")[: max(checks_left, 0)]:
        checks_left -= 1
        found = _buyer_has_item(deal)
        with transaction.atomic():
            locked = _locked(deal.code)
            locked.proof_checked_at = now
            if found and locked.status == Deal.Status.SENT:
                locked.proof_at = now
                _start_hold(locked, now=now)
                report["proof"] += 1
                log_event(MarketEvent.Kind.DEAL_PROOF, user=locked.seller, actor=None, deal=locked, message=f"{locked.code}: предмет найден в инвентаре покупателя, удержание до {timezone.localtime(locked.hold_until):%d.%m %H:%M}")
                transaction.on_commit(lambda d=locked: (_notify(d.buyer, "proof", d), _notify(d.seller, "proof", d)))
            locked.save()

    for deal in Deal.objects.select_related("listing", "buyer", "seller").filter(status=Deal.Status.HOLD, hold_until__lte=now):
        # Proven by the inventory only (the buyer never confirmed): make sure the trade was not reversed.
        if deal.accepted_at is None:
            if checks_left <= 0:
                continue
            checks_left -= 1
            found = _buyer_has_item(deal)
            if found is None:
                continue  # try again on the next run
            if not found:
                with transaction.atomic():
                    locked = _locked(deal.code)
                    if locked.status == Deal.Status.HOLD:
                        locked.status_before_dispute, locked.status, locked.disputed_at = locked.status, Deal.Status.DISPUTED, now
                        locked.dispute_reason = "Автоматически: к концу удержания предмета нет в инвентаре покупателя (возможен откат обмена)."
                        locked.save()
                        log_event(MarketEvent.Kind.DEAL_DISPUTED, user=locked.seller, actor=None, deal=locked, message=f"{locked.code}: предмет пропал у покупателя за время удержания — спор открыт автоматически")
                        transaction.on_commit(lambda d=locked: _notify_admins(d))
                        report["auto_disputed"] += 1
                continue
        with transaction.atomic():
            locked = _locked(deal.code)
            if locked.status != Deal.Status.HOLD:
                continue
            _complete(locked)
            locked.save()
            log_event(MarketEvent.Kind.DEAL_COMPLETED, user=locked.seller, actor=None, deal=locked, message=f"{locked.code}: удержание прошло, {locked.price:.2f} ₽ зачислены продавцу", payload={"price": str(locked.price)})
            transaction.on_commit(lambda d=locked: (_notify(d.seller, "completed", d), _notify(d.buyer, "completed", d)))
            report["completed"] += 1
    return report


# --------------------------------------------------------------------------- reading


def held_amount(user) -> Decimal:
    """Money of the user's sales still held by open deals (paid by buyers, not yet released)."""
    from django.db.models import Sum

    return Deal.objects.filter(seller=user, status__in=Deal.OPEN_STATUSES).aggregate(total=Sum("price"))["total"] or Decimal("0.00")


def deal_history(deal: Deal):
    return deal.events.select_related("actor").order_by("created_at", "id")
