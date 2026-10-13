from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.balance import BalanceError, adjust_balance

from .models import ZERO, SimCase, SimCaseItem, SimOpening, TerminalSession

# Official CS2 drop odds per rarity tier. A tier's chance is split evenly between its items.
RARITY_ODDS = {
    SimCaseItem.Rarity.CONSUMER: Decimal("0.7992"),
    SimCaseItem.Rarity.INDUSTRIAL: Decimal("0.7992"),
    SimCaseItem.Rarity.MILSPEC: Decimal("0.7992"),
    SimCaseItem.Rarity.RESTRICTED: Decimal("0.1598"),
    SimCaseItem.Rarity.CLASSIFIED: Decimal("0.0320"),
    SimCaseItem.Rarity.COVERT: Decimal("0.0064"),
    SimCaseItem.Rarity.GOLD: Decimal("0.0026"),
}
RARITY_ORDER = [
    SimCaseItem.Rarity.GOLD,
    SimCaseItem.Rarity.COVERT,
    SimCaseItem.Rarity.CLASSIFIED,
    SimCaseItem.Rarity.RESTRICTED,
    SimCaseItem.Rarity.MILSPEC,
    SimCaseItem.Rarity.INDUSTRIAL,
    SimCaseItem.Rarity.CONSUMER,
]
EXTERIORS = (
    (Decimal("0.07"), "Factory New", Decimal("1.30")),
    (Decimal("0.15"), "Minimal Wear", Decimal("1.10")),
    (Decimal("0.38"), "Field-Tested", Decimal("1.00")),
    (Decimal("0.45"), "Well-Worn", Decimal("0.85")),
    (Decimal("1.01"), "Battle-Scarred", Decimal("0.75")),
)
EXTERIOR_SHORT = {
    "Factory New": "FN",
    "Minimal Wear": "MW",
    "Field-Tested": "FT",
    "Well-Worn": "WW",
    "Battle-Scarred": "BS",
}
STATTRAK_CHANCE = 0.10
STATTRAK_MULTIPLIER = Decimal("1.25")
MAX_OPEN_COUNT = 5
STRIP_LENGTH = 56
STRIP_WIN_INDEX = 48

_rng = random.SystemRandom()


class SimulatorError(Exception):
    pass


@dataclass(frozen=True)
class Roll:
    item: SimCaseItem
    float_value: Decimal | None
    exterior: str
    is_stattrak: bool
    value: Decimal


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def item_weights(items: list[SimCaseItem]) -> list[tuple[SimCaseItem, Decimal]]:
    """Per-item drop weights (sum to 1). Explicit weights win; otherwise CS2 tier odds."""
    if not items:
        return []
    tier_sizes: dict[str, int] = {}
    for item in items:
        if item.weight is None:
            tier_sizes[item.rarity] = tier_sizes.get(item.rarity, 0) + 1

    raw = []
    for item in items:
        if item.weight is not None:
            weight = Decimal(item.weight)
        else:
            weight = RARITY_ODDS.get(item.rarity, Decimal("0.01")) / tier_sizes[item.rarity]
        raw.append((item, max(weight, Decimal("0"))))

    total = sum((weight for _, weight in raw), Decimal("0"))
    if total <= 0:
        share = Decimal(1) / len(raw)
        return [(item, share) for item, _ in raw]
    return [(item, weight / total) for item, weight in raw]


def drop_chances(case: SimCase) -> list[dict]:
    """Contents of a case grouped by rarity with chance in percent, best rarity first."""
    items = list(case.items.all())
    weights = item_weights(items)
    rows = []
    for item, weight in weights:
        rows.append({"item": item, "chance": (weight * 100).quantize(Decimal("0.001"))})
    order = {rarity: index for index, rarity in enumerate(RARITY_ORDER)}
    rows.sort(key=lambda row: (order.get(row["item"].rarity, 99), -row["item"].value, row["item"].name))
    return rows


def rarity_summary(case: SimCase) -> list[dict]:
    totals: dict[str, Decimal] = {}
    for item, weight in item_weights(list(case.items.all())):
        totals[item.rarity] = totals.get(item.rarity, Decimal("0")) + weight
    labels = dict(SimCaseItem.Rarity.choices)
    return [
        {"rarity": rarity, "css": rarity, "label": labels[rarity], "chance": (totals[rarity] * 100).quantize(Decimal("0.01"))}
        for rarity in RARITY_ORDER
        if rarity in totals
    ]


def _pick(weights: list[tuple[SimCaseItem, Decimal]]) -> SimCaseItem:
    point = Decimal(str(_rng.random()))
    cumulative = Decimal("0")
    for item, weight in weights:
        cumulative += weight
        if point < cumulative:
            return item
    return weights[-1][0]


def _exterior_for(float_value: Decimal | None) -> tuple[str, Decimal]:
    if float_value is None:
        return "", Decimal("1.00")
    for threshold, label, multiplier in EXTERIORS:
        if float_value < threshold:
            return label, multiplier
    return EXTERIORS[-1][1], EXTERIORS[-1][2]


def _can_be_stattrak(item: SimCaseItem) -> bool:
    if not item.stattrak_allowed:
        return False
    name = item.name.lower()
    return "gloves" not in name and "wraps" not in name


def roll_item(item: SimCaseItem) -> Roll:
    float_value = None
    if item.has_float:
        low, high = float(item.min_float), float(item.max_float)
        float_value = Decimal(str(round(_rng.uniform(low, high), 6)))
    exterior, wear_multiplier = _exterior_for(float_value)
    is_stattrak = _can_be_stattrak(item) and _rng.random() < STATTRAK_CHANCE
    value = Decimal(item.value or ZERO) * wear_multiplier
    if is_stattrak:
        value *= STATTRAK_MULTIPLIER
    return Roll(item=item, float_value=float_value, exterior=exterior, is_stattrak=is_stattrak, value=_money(value))


def roll_case(case: SimCase) -> Roll:
    weights = item_weights(list(case.items.all()))
    if not weights:
        raise SimulatorError("В кейсе пока нет предметов.")
    return roll_item(_pick(weights))


def build_strip(case: SimCase, winner: SimCaseItem, *, length: int = STRIP_LENGTH, win_index: int = STRIP_WIN_INDEX) -> list[SimCaseItem]:
    weights = item_weights(list(case.items.all()))
    strip = [_pick(weights) for _ in range(length)]
    strip[win_index] = winner
    return strip


def _charge(*, user, amount: Decimal, opening_hint: str) -> None:
    try:
        adjust_balance(
            user=user,
            delta=-_money(amount),
            reason=LedgerReason.CASE_OPEN,
            amount_snapshot=_money(amount),
            idempotency_key=f"{opening_hint}:{uuid.uuid4().hex}",
        )
    except BalanceError as exc:
        raise SimulatorError("Недостаточно средств на балансе.") from exc


@transaction.atomic
def open_case(*, user, case: SimCase, count: int = 1) -> list[SimOpening]:
    if case.is_terminal:
        raise SimulatorError("Терминал открывается через отдельный экран.")
    if not case.is_active:
        raise SimulatorError("Кейс сейчас недоступен.")
    count = max(1, min(int(count or 1), MAX_OPEN_COUNT))

    total = case.open_price * count
    if total > ZERO:
        _charge(user=user, amount=total, opening_hint=f"sim_case:{case.pk}")

    openings = []
    for _ in range(count):
        roll = roll_case(case)
        openings.append(SimOpening.objects.create(
            user=user,
            case=case,
            item=roll.item,
            source=SimOpening.Source.CASE,
            float_value=roll.float_value,
            exterior=roll.exterior,
            is_stattrak=roll.is_stattrak,
            price_paid=_money(case.open_price),
            item_value=roll.value,
        ))
    return openings


@transaction.atomic
def sell_openings(*, user, opening_ids=None, rarity: str = "") -> tuple[int, Decimal]:
    """Sell inventory items back for their estimated value. Returns (sold_count, credited_total).

    opening_ids=None sells everything still in the inventory (optionally only one rarity).
    """
    queryset = SimOpening.objects.select_for_update().filter(user=user, sold_at__isnull=True)
    if opening_ids is not None:
        ids = {int(pk) for pk in opening_ids if str(pk).isdigit()}
        queryset = queryset.filter(pk__in=ids)
    elif rarity:
        queryset = queryset.filter(item__rarity=rarity)
    openings = list(queryset.order_by("id"))
    if not openings:
        raise SimulatorError("Нечего продавать: предметы уже проданы или не выбраны.")

    now = timezone.now()
    total = ZERO
    for opening in openings:
        price = _money(opening.item_value or ZERO)
        if price > ZERO:
            adjust_balance(
                user=user,
                delta=price,
                reason=LedgerReason.ITEM_SALE,
                amount_snapshot=price,
                case_opening_id=opening.pk,
                idempotency_key=f"sim_sell:{opening.pk}",
            )
        opening.sold_at = now
        opening.sold_price = price
        total += price
    SimOpening.objects.bulk_update(openings, ["sold_at", "sold_price"])
    return len(openings), total


def _offer_price(case: SimCase, roll: Roll) -> Decimal:
    markup = Decimal(100 + int(case.terminal_markup_percent or 0)) / Decimal(100)
    return _money(roll.value * markup)


@transaction.atomic
def start_terminal(*, user, case: SimCase) -> TerminalSession:
    if not case.is_terminal:
        raise SimulatorError("Это не терминал.")
    if not case.is_active:
        raise SimulatorError("Терминал сейчас недоступен.")

    active = TerminalSession.objects.select_for_update().filter(user=user, case=case, status=TerminalSession.Status.ACTIVE).first()
    if active is not None:
        return active

    if case.open_price > ZERO:
        _charge(user=user, amount=case.open_price, opening_hint=f"sim_terminal:{case.pk}")

    offers = []
    for _ in range(max(1, case.terminal_offers)):
        roll = roll_case(case)
        offers.append({
            "item_id": roll.item.pk,
            "float": str(roll.float_value) if roll.float_value is not None else None,
            "exterior": roll.exterior,
            "stattrak": roll.is_stattrak,
            "value": str(roll.value),
            "price": str(_offer_price(case, roll)),
        })
    return TerminalSession.objects.create(user=user, case=case, offers=offers, price_paid=_money(case.open_price))


def _locked_session(*, user, session_id: int) -> TerminalSession:
    session = TerminalSession.objects.select_for_update().select_related("case").filter(pk=session_id, user=user).first()
    if session is None:
        raise SimulatorError("Сессия терминала не найдена.")
    if session.status != TerminalSession.Status.ACTIVE or session.current_offer is None:
        raise SimulatorError("Этот терминал уже закрыт.")
    return session


@transaction.atomic
def accept_terminal_offer(*, user, session_id: int) -> SimOpening:
    session = _locked_session(user=user, session_id=session_id)
    offer = session.current_offer
    price = Decimal(offer["price"])
    if price > ZERO:
        _charge(user=user, amount=price, opening_hint=f"sim_terminal_offer:{session.pk}")

    item = SimCaseItem.objects.get(pk=offer["item_id"])
    opening = SimOpening.objects.create(
        user=user,
        case=session.case,
        item=item,
        source=SimOpening.Source.TERMINAL,
        float_value=Decimal(offer["float"]) if offer.get("float") else None,
        exterior=offer.get("exterior") or "",
        is_stattrak=bool(offer.get("stattrak")),
        price_paid=price,
        item_value=Decimal(offer["value"]),
    )
    session.status = TerminalSession.Status.ACCEPTED
    session.opening = opening
    session.save(update_fields=["status", "opening", "updated_at"])
    return opening


@transaction.atomic
def decline_terminal_offer(*, user, session_id: int) -> TerminalSession:
    session = _locked_session(user=user, session_id=session_id)
    session.current_index += 1
    if session.current_index >= len(session.offers):
        session.status = TerminalSession.Status.BURNED
    session.save(update_fields=["current_index", "status", "updated_at"])
    return session


def item_payload(item: SimCaseItem) -> dict:
    return {
        "id": item.pk,
        "name": item.name,
        "skin": item.skin_name,
        "weapon": item.weapon_label,
        "rarity": item.rarity_css,
        "image": item.image_url,
    }


def demo_payload(roll: Roll) -> dict:
    return {
        **item_payload(roll.item),
        "opening_id": None,
        "display_name": ("StatTrak™ " if roll.is_stattrak else "") + roll.item.name + (f" ({roll.exterior})" if roll.exterior else ""),
        "exterior": roll.exterior,
        "exterior_short": EXTERIOR_SHORT.get(roll.exterior, ""),
        "float": str(roll.float_value) if roll.float_value is not None else None,
        "stattrak": roll.is_stattrak,
        "value": str(roll.value),
    }


def roll_payload(opening: SimOpening) -> dict:
    return {
        **item_payload(opening.item),
        "opening_id": opening.pk,
        "display_name": opening.display_name,
        "exterior": opening.exterior,
        "exterior_short": EXTERIOR_SHORT.get(opening.exterior, ""),
        "float": str(opening.float_value) if opening.float_value is not None else None,
        "stattrak": opening.is_stattrak,
        "value": str(opening.item_value),
    }


def offer_payload(session: TerminalSession) -> dict | None:
    offer = session.current_offer
    if offer is None:
        return None
    item = SimCaseItem.objects.get(pk=offer["item_id"])
    return {
        **item_payload(item),
        "exterior": offer.get("exterior") or "",
        "exterior_short": EXTERIOR_SHORT.get(offer.get("exterior") or "", ""),
        "float": offer.get("float"),
        "stattrak": bool(offer.get("stattrak")),
        "value": offer["value"],
        "price": offer["price"],
        "index": session.current_index + 1,
        "total": len(session.offers),
    }


DATA_FILE = Path(__file__).resolve().parent / "data" / "cases.json"


def load_case_rows(path: Path | None = None) -> list[dict]:
    with open(path or DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def seed_cases(rows: list[dict], *, update_existing: bool = False, case_model=None, item_model=None) -> tuple[int, int]:
    """Create simulator cases from fixture rows. Returns (created_cases, created_items).

    Model classes can be swapped for historical ones so data migrations can reuse this.
    """
    SimCase = case_model or globals()["SimCase"]  # noqa: N806
    SimCaseItem = item_model or globals()["SimCaseItem"]  # noqa: N806
    created_cases = created_items = 0
    for order, row in enumerate(rows):
        defaults = {
            "name": row["name"],
            "kind": row["kind"],
            "description": row.get("description", ""),
            "collection_name": row.get("collection", ""),
            "case_price": Decimal(str(row.get("case_price", 0))),
            "key_price": Decimal(str(row.get("key_price", 0))),
            "image_url": row.get("image", ""),
            "accent_color": row.get("accent", "#f5a623"),
            "sort_order": (order + 1) * 10,
        }
        case = SimCase.objects.filter(slug=row["slug"]).first()
        if case is None:
            case = SimCase.objects.create(slug=row["slug"], **defaults)
            created_cases += 1
        elif update_existing:
            for field, value in defaults.items():
                setattr(case, field, value)
            case.save()
        else:
            continue

        if update_existing:
            case.items.all().delete()
        for index, item in enumerate(row.get("items", [])):
            SimCaseItem.objects.create(
                case=case,
                name=item["name"],
                weapon=item.get("weapon", ""),
                rarity=item.get("rarity", "milspec"),
                image_url=item.get("image", ""),
                value=Decimal(str(item.get("value", 0))),
                min_float=Decimal(str(item["min_float"])) if item.get("min_float") is not None else None,
                max_float=Decimal(str(item["max_float"])) if item.get("max_float") is not None else None,
                stattrak_allowed=bool(item.get("stattrak", True)),
                sort_order=index,
            )
            created_items += 1
    return created_cases, created_items
