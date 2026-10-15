from __future__ import annotations

import html
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.models import PriceDropAlertEvent, PriceDropAlertRule, SteamItem, SteamItemPriceHistory


def _quantize_percent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _build_movement_candidate(*, history_rows, direction: str):
    if len(history_rows) < 2:
        return None

    current_entry = history_rows[-1]
    previous_rows = history_rows[:-1]
    if not previous_rows:
        return None

    if direction == PriceDropAlertEvent.Direction.UP:
        baseline_entry = min(previous_rows, key=lambda entry: (entry.price_rub, entry.created_at))
        price_delta = current_entry.price_rub - baseline_entry.price_rub
    else:
        baseline_entry = max(previous_rows, key=lambda entry: (entry.price_rub, entry.created_at))
        price_delta = current_entry.price_rub - baseline_entry.price_rub

    if current_entry.created_at <= baseline_entry.created_at:
        return None
    if baseline_entry.price_rub == Decimal("0"):
        return None
    if price_delta == Decimal("0"):
        return None
    if direction == PriceDropAlertEvent.Direction.UP and price_delta <= Decimal("0"):
        return None
    if direction == PriceDropAlertEvent.Direction.DOWN and price_delta >= Decimal("0"):
        return None

    movement_rub = price_delta.copy_abs()
    movement_percent = (movement_rub / baseline_entry.price_rub) * Decimal("100")

    return {
        "baseline_entry": baseline_entry,
        "current_entry": current_entry,
        "change_rub": movement_rub,
        "change_percent": _quantize_percent(movement_percent),
        "signed_change_rub": price_delta,
        "signed_change_percent": _quantize_percent((price_delta / baseline_entry.price_rub) * Decimal("100")),
    }


def _get_repeat_step_index(*, rule: PriceDropAlertRule, change_rub: Decimal, change_percent: Decimal) -> int:
    step_index = 1

    if rule.repeat_step_rub and rule.repeat_step_rub > Decimal("0"):
        rub_step = int(((change_rub - rule.min_change_rub) / rule.repeat_step_rub)) + 1
        step_index = min(step_index, rub_step) if step_index > 1 else rub_step

    if rule.repeat_step_percent and rule.repeat_step_percent > Decimal("0"):
        percent_step = int(((change_percent - rule.min_change_percent) / rule.repeat_step_percent)) + 1
        step_index = min(step_index, percent_step) if step_index > 1 else percent_step

    return max(1, step_index)


def _get_last_movement_event(*, rule: PriceDropAlertRule, item: SteamItem, direction: str):
    return (
        PriceDropAlertEvent.objects
        .filter(rule=rule, item=item, direction=direction)
        .exclude(status=PriceDropAlertEvent.Status.IGNORED)
        .order_by("-created_at", "-id")
        .first()
    )


def _should_skip_movement_event(*, rule: PriceDropAlertRule, item: SteamItem, direction: str, baseline_entry, step_index: int, now):
    last_event = _get_last_movement_event(rule=rule, item=item, direction=direction)
    if last_event is None:
        return False

    if last_event.baseline_at == baseline_entry.created_at and last_event.step_index >= step_index:
        return True

    if (
        last_event.baseline_at != baseline_entry.created_at
        and step_index == 1
        and rule.cooldown_minutes > 0
        and last_event.created_at >= now - timedelta(minutes=rule.cooldown_minutes)
    ):
        return True

    return False


def detect_price_movement_alerts_for_category(item_type: str, *, trigger_source: str = "celery", item_ids=None) -> int:
    """``item_ids`` limits the check to items whose price just changed (the price queue passes them)."""
    now = timezone.now()
    created_count = 0

    rules = list(PriceDropAlertRule.objects.filter(item_type=item_type, is_enabled=True).order_by("id"))
    if not rules:
        return 0

    items_qs = SteamItem.objects.filter(item_type=item_type, is_active=True, price_rub__isnull=False)
    if item_ids is not None:
        items_qs = items_qs.filter(pk__in=list(item_ids))
    items = list(items_qs.only("id", "market_hash_name", "item_type", "price_rub", "name", "name_ru").order_by("id"))

    for rule in rules:
        if rule.direction == PriceDropAlertRule.Direction.BOTH:
            directions = [PriceDropAlertEvent.Direction.UP, PriceDropAlertEvent.Direction.DOWN]
        else:
            directions = [rule.direction]

        window_started_at = now - timedelta(hours=rule.lookback_hours)

        for item in items:
            history_rows = list(
                SteamItemPriceHistory.objects
                .filter(item=item, created_at__gte=window_started_at)
                .order_by("created_at", "id")
            )
            if len(history_rows) < 2:
                continue

            for direction in directions:
                candidate = _build_movement_candidate(history_rows=history_rows, direction=direction)
                if candidate is None:
                    continue

                change_rub = candidate["change_rub"]
                change_percent = candidate["change_percent"]
                baseline_entry = candidate["baseline_entry"]
                current_entry = candidate["current_entry"]

                if change_rub < rule.min_change_rub:
                    continue
                if change_percent < rule.min_change_percent:
                    continue

                step_index = _get_repeat_step_index(
                    rule=rule,
                    change_rub=change_rub,
                    change_percent=change_percent,
                )
                if _should_skip_movement_event(
                    rule=rule,
                    item=item,
                    direction=direction,
                    baseline_entry=baseline_entry,
                    step_index=step_index,
                    now=now,
                ):
                    continue

                signed_change_rub = candidate["signed_change_rub"].quantize(Decimal("0.01"))
                signed_change_percent = candidate["signed_change_percent"]

                PriceDropAlertEvent.objects.create(
                    rule=rule,
                    item=item,
                    notify_transport=rule.notify_transport,
                    trigger_source=(trigger_source or "celery")[:16],
                    status=PriceDropAlertEvent.Status.PENDING,
                    direction=direction,
                    window_started_at=window_started_at,
                    baseline_at=baseline_entry.created_at,
                    current_at=current_entry.created_at,
                    baseline_price_rub=baseline_entry.price_rub,
                    current_price_rub=current_entry.price_rub,
                    drop_rub=signed_change_rub,
                    drop_percent=signed_change_percent,
                    step_index=step_index,
                    payload={
                        "item_type": item_type,
                        "item_id": item.id,
                        "market_hash_name": item.market_hash_name,
                        "baseline_source": baseline_entry.source,
                        "current_source": current_entry.source,
                        "lookback_hours": rule.lookback_hours,
                        "cooldown_minutes": rule.cooldown_minutes,
                        "repeat_step_rub": str(rule.repeat_step_rub),
                        "repeat_step_percent": str(rule.repeat_step_percent),
                    },
                )
                created_count += 1

    return created_count


def detect_price_drop_alerts_for_category(item_type: str, *, trigger_source: str = "celery", item_ids=None) -> int:
    return detect_price_movement_alerts_for_category(item_type, trigger_source=trigger_source, item_ids=item_ids)


def _build_item_absolute_url(item: SteamItem) -> str:
    from modules.v2.system.services import get_public_base_url

    path = reverse("steam_catalog:detail", args=[item.id])
    base_url = (get_public_base_url() or getattr(settings, "SITE_BASE_URL", "") or "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}{path}"


def _build_price_drop_alert_message(event: PriceDropAlertEvent) -> str:
    item = event.item
    item_label = html.escape(item.display_name)
    item_url = html.escape(_build_item_absolute_url(item), quote=True)
    baseline_label = html.escape(timezone.localtime(event.baseline_at).strftime("%d.%m.%Y %H:%M"))
    current_label = html.escape(timezone.localtime(event.current_at).strftime("%d.%m.%Y %H:%M"))

    if event.direction == PriceDropAlertEvent.Direction.UP:
        header = "📈 <b>Рост цены</b>"
        delta_title = "Рост"
    else:
        header = "📉 <b>Падение цены</b>"
        delta_title = "Падение"

    return (
        f"{header}\n\n"
        f"<b>Предмет:</b> <a href=\"{item_url}\">{item_label}</a>\n"
        f"<b>Категория:</b> {html.escape(item.get_item_type_display())}\n"
        f"<b>Старая цена:</b> {event.baseline_price_rub} ₽ ({baseline_label})\n"
        f"<b>Актуальная цена:</b> {event.current_price_rub} ₽ ({current_label})\n"
        f"<b>{delta_title}:</b> {event.drop_rub:+.2f} ₽ ({event.drop_percent:+.2f}%)\n"
        f"<b>Шаг:</b> #{event.step_index}\n"
        f"<b>Окно анализа:</b> {event.rule.lookback_hours} ч\n"
        f"<b>Источник:</b> {html.escape(event.trigger_source)}"
    )


__all__ = [
    "detect_price_drop_alerts_for_category",
    "detect_price_movement_alerts_for_category",
    "_build_price_drop_alert_message",
]
