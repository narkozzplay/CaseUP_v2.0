from __future__ import annotations

import html
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.system.services import get_public_base_url

from ..models import SteamItem, SteamItemPriceHistory, UserPriceAlertEvent, UserPriceAlertRule


HTML_PARSE_MODE = "HTML"


def _quantize_percent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _window_start(rule: UserPriceAlertRule, now):
    if rule.window_kind == UserPriceAlertRule.WindowKind.HOURS:
        return now - timedelta(hours=rule.window_value)
    if rule.window_kind == UserPriceAlertRule.WindowKind.DAYS:
        return now - timedelta(days=rule.window_value)
    return None


def _history_for_rule_item(rule: UserPriceAlertRule, item: SteamItem):
    qs = SteamItemPriceHistory.objects.filter(item=item).order_by("created_at", "id")
    start_at = _window_start(rule, timezone.now())
    if start_at is not None:
        qs = qs.filter(created_at__gte=start_at)
    rows = list(qs)
    if rule.window_kind == UserPriceAlertRule.WindowKind.POINTS:
        rows = rows[-rule.window_value:]
    return rows


def _find_last_local_extreme(rows, *, kind: str):
    if len(rows) < 3:
        return None

    found = []
    for idx in range(1, len(rows) - 1):
        prev_row, row, next_row = rows[idx - 1], rows[idx], rows[idx + 1]

        if kind == "max" and row.price_rub >= prev_row.price_rub and row.price_rub > next_row.price_rub:
            found.append(row)

        if kind == "min" and row.price_rub <= prev_row.price_rub and row.price_rub < next_row.price_rub:
            found.append(row)

    return found[-1] if found else None


def _resolve_reference_entry(rule: UserPriceAlertRule, rows, direction: str):
    previous_rows = rows[:-1]
    if len(previous_rows) < 1:
        return None

    ref = rule.reference_point

    if ref == UserPriceAlertRule.ReferencePoint.PREVIOUS_POINT:
        return previous_rows[-1]

    if ref == UserPriceAlertRule.ReferencePoint.WINDOW_START:
        return rows[0]

    if ref == UserPriceAlertRule.ReferencePoint.WINDOW_MAX:
        return max(previous_rows, key=lambda row: (row.price_rub, row.created_at))

    if ref == UserPriceAlertRule.ReferencePoint.WINDOW_MIN:
        return min(previous_rows, key=lambda row: (row.price_rub, row.created_at))

    if ref == UserPriceAlertRule.ReferencePoint.LAST_LOCAL_MAX:
        return _find_last_local_extreme(rows, kind="max")

    if ref == UserPriceAlertRule.ReferencePoint.LAST_LOCAL_MIN:
        return _find_last_local_extreme(rows, kind="min")

    if ref == UserPriceAlertRule.ReferencePoint.LAST_EXTREME:
        if direction == UserPriceAlertRule.Direction.DOWN:
            return _find_last_local_extreme(rows, kind="max") or max(
                previous_rows, key=lambda row: (row.price_rub, row.created_at)
            )
        return _find_last_local_extreme(rows, kind="min") or min(
            previous_rows, key=lambda row: (row.price_rub, row.created_at)
        )

    return None


def _candidate_for_direction(rule: UserPriceAlertRule, rows, direction: str):
    if len(rows) < 2:
        return None

    current = rows[-1]
    baseline = _resolve_reference_entry(rule, rows, direction)

    if baseline is None or baseline.created_at >= current.created_at or baseline.price_rub == Decimal("0"):
        return None

    delta = current.price_rub - baseline.price_rub

    if direction == UserPriceAlertRule.Direction.UP and delta <= 0:
        return None

    if direction == UserPriceAlertRule.Direction.DOWN and delta >= 0:
        return None

    abs_change = delta.copy_abs().quantize(Decimal("0.01"))

    return {
        "baseline": baseline,
        "current": current,
        "change_rub": abs_change,
        "change_percent": _quantize_percent((abs_change / baseline.price_rub) * Decimal("100")),
        "signed_change_rub": delta.quantize(Decimal("0.01")),
        "signed_change_percent": _quantize_percent((delta / baseline.price_rub) * Decimal("100")),
    }


def _passes_threshold(rule: UserPriceAlertRule, change_rub: Decimal, change_percent: Decimal) -> bool:
    rub_ok = rule.min_change_rub > 0 and change_rub >= rule.min_change_rub
    pct_ok = rule.min_change_percent > 0 and change_percent >= rule.min_change_percent

    if rule.threshold_mode == UserPriceAlertRule.ThresholdMode.RUB:
        return rub_ok

    if rule.threshold_mode == UserPriceAlertRule.ThresholdMode.PERCENT:
        return pct_ok

    if rule.threshold_mode == UserPriceAlertRule.ThresholdMode.OR:
        return rub_ok or pct_ok

    return rub_ok and pct_ok


def _last_event(rule, item, direction):
    return (
        rule.events
        .filter(item=item, direction=direction)
        .order_by("-created_at", "-id")
        .first()
    )


def _repeat_decision(rule, item, direction, baseline, change_rub, change_percent):
    last = _last_event(rule, item, direction)

    if last is None:
        return 1, "initial"

    if rule.cooldown_minutes > 0 and last.created_at >= timezone.now() - timedelta(minutes=rule.cooldown_minutes):
        return None, "cooldown"

    if rule.repeat_mode == UserPriceAlertRule.RepeatMode.ONCE:
        return None, "once"

    if rule.repeat_mode == UserPriceAlertRule.RepeatMode.NEW_EXTREME:
        if last.reference_at == baseline.created_at:
            return None, "same_extreme"
        return 1, "new_extreme"

    step_index = 1

    if rule.repeat_step_rub > 0:
        step_index = max(step_index, int(((change_rub - rule.min_change_rub) / rule.repeat_step_rub)) + 1)

    if rule.repeat_step_percent > 0:
        step_index = max(step_index, int(((change_percent - rule.min_change_percent) / rule.repeat_step_percent)) + 1)

    if last.reference_at == baseline.created_at and step_index <= last.step_index:
        return None, "duplicate_step"

    return max(step_index, 1), "step"


def _iter_rule_items(rule, item_type):
    qs = SteamItem.objects.filter(is_active=True, price_rub__isnull=False)

    if rule.scope_type == UserPriceAlertRule.ScopeType.ITEM:
        return list(qs.filter(pk=rule.item_id, item_type=item_type))

    if rule.scope_type == UserPriceAlertRule.ScopeType.FAMILY:
        family_qs = qs.filter(family_name=rule.family_name)
        if rule.item_type:
            family_qs = family_qs.filter(item_type=item_type)
        return list(family_qs.order_by("id"))

    return list(qs.filter(item_type=item_type).order_by("id"))


def evaluate_user_price_alerts_for_item_type(item_type: str, *, trigger_source: str = "celery") -> int:
    created_count = 0

    rules = list(
        UserPriceAlertRule.objects
        .filter(status=UserPriceAlertRule.Status.ACTIVE)
        .select_related("user", "item")
        .order_by("id")
    )

    for rule in rules:
        if rule.scope_type == UserPriceAlertRule.ScopeType.ITEM_TYPE and rule.item_type != item_type:
            continue

        if rule.scope_type == UserPriceAlertRule.ScopeType.ITEM and getattr(rule.item, "item_type", None) != item_type:
            continue

        directions = (
            [rule.direction]
            if rule.direction != UserPriceAlertRule.Direction.BOTH
            else [UserPriceAlertRule.Direction.UP, UserPriceAlertRule.Direction.DOWN]
        )

        for item in _iter_rule_items(rule, item_type):
            rows = _history_for_rule_item(rule, item)
            if len(rows) < 2:
                continue

            for direction in directions:
                candidate = _candidate_for_direction(rule, rows, direction)
                if not candidate:
                    continue

                if not _passes_threshold(rule, candidate["change_rub"], candidate["change_percent"]):
                    continue

                step_index, repeat_reason = _repeat_decision(
                    rule,
                    item,
                    direction,
                    candidate["baseline"],
                    candidate["change_rub"],
                    candidate["change_percent"],
                )
                if step_index is None:
                    continue

                dedupe_key = (
                    f"{rule.id}:{item.id}:{direction}:"
                    f"{candidate['baseline'].created_at.isoformat()}:"
                    f"{candidate['current'].created_at.isoformat()}:"
                    f"{step_index}"
                )

                try:
                    with transaction.atomic():
                        UserPriceAlertEvent.objects.create(
                            rule=rule,
                            user=rule.user,
                            item=item,
                            transport=rule.transport,
                            delivery_target=rule.delivery_target,
                            delivery_status=UserPriceAlertEvent.Status.PENDING,
                            trigger_source=(trigger_source or "celery")[:16],
                            direction=direction,
                            reference_point=rule.reference_point,
                            reference_at=candidate["baseline"].created_at,
                            current_at=candidate["current"].created_at,
                            window_started_at=rows[0].created_at,
                            baseline_price_rub=candidate["baseline"].price_rub,
                            current_price_rub=candidate["current"].price_rub,
                            change_rub=candidate["signed_change_rub"],
                            change_percent=candidate["signed_change_percent"],
                            step_index=step_index,
                            repeat_reason=repeat_reason,
                            dedupe_key=dedupe_key,
                            payload={
                                "window_kind": rule.window_kind,
                                "window_value": rule.window_value,
                                "threshold_mode": rule.threshold_mode,
                            },
                        )
                        rule.last_triggered_at = timezone.now()
                        rule.last_error_message = ""
                        rule.save(update_fields=["last_triggered_at", "last_error_message", "updated_at"])
                        created_count += 1
                except Exception:
                    continue

    return created_count


def _build_item_absolute_url(item: SteamItem) -> str:
    base_url = (get_public_base_url() or "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}{reverse('steam_catalog:detail', args=[item.id])}"


def _build_user_price_alert_message(event: UserPriceAlertEvent) -> str:
    direction_title = "Рост" if event.direction == UserPriceAlertRule.Direction.UP else "Падение"

    return (
        f"<b>Price alert</b>\n\n"
        f"<b>Правило:</b> {html.escape(event.rule.name)}\n"
        f"<b>Предмет:</b> "
        f"<a href=\"{html.escape(_build_item_absolute_url(event.item), quote=True)}\">"
        f"{html.escape(event.item.group_name)}</a>\n"
        f"<b>Направление:</b> {direction_title}\n"
        f"<b>Reference:</b> {html.escape(event.rule.get_reference_point_display())}\n"
        f"<b>База:</b> {event.baseline_price_rub} ₽ "
        f"({timezone.localtime(event.reference_at).strftime('%d.%m.%Y %H:%M')})\n"
        f"<b>Текущая:</b> {event.current_price_rub} ₽ "
        f"({timezone.localtime(event.current_at).strftime('%d.%m.%Y %H:%M')})\n"
        f"<b>Изменение:</b> {event.change_rub:+.2f} ₽ ({event.change_percent:+.2f}%)\n"
        f"<b>Шаг:</b> #{event.step_index} / {html.escape(event.repeat_reason or 'initial')}"
    )


def _resolve_chat_id(event: UserPriceAlertEvent) -> str:
    if event.delivery_target == UserPriceAlertRule.DeliveryTarget.DEFAULT_CHAT:
        from modules.v2.tg_notifier.services import resolve_default_chat_id
        return (resolve_default_chat_id() or "").strip()

    profile = UserProfile.objects.filter(user=event.user).only("telegram_chat_id").first()
    return (getattr(profile, "telegram_chat_id", "") or "").strip()


def _send_telegram_message(*, text: str, chat_id: str, parse_mode: str | None = None):
    from modules.v2.tg_notifier.services import send_message
    return send_message(text=text, chat_id=chat_id, parse_mode=parse_mode)


def _notify_on_site(event: UserPriceAlertEvent) -> None:
    from django.urls import reverse

    from modules.v2.notifications.services import notify
    from modules.v2.steam_catalog.notifications import KIND_PRICE_ALERT

    arrow = "▲" if event.direction == UserPriceAlertRule.Direction.UP else "▼"
    notify(
        event.user,
        KIND_PRICE_ALERT,
        object_id=event.pk,
        dedupe=False,
        payload={
            "title": f"{arrow} {event.item.group_name}: {event.change_percent:+.2f}%",
            "body": f"{event.rule.name}: {event.baseline_price_rub} → {event.current_price_rub} ₽ ({event.change_rub:+.2f} ₽)",
            "url": reverse("steam_catalog:detail", args=[event.item_id]),
        },
    )


def _deliver(event: UserPriceAlertEvent) -> tuple[str, str, dict]:
    """Sends one alert through the rule's channels; returns (sent_to, error, telegram payload)."""
    Transport = UserPriceAlertRule.Transport
    sent_to, errors, telegram_payload = [], [], {}
    if event.transport in (Transport.SITE, Transport.BOTH):
        _notify_on_site(event)
        sent_to.append("site")
    if event.transport in (Transport.TELEGRAM, Transport.BOTH):
        chat_id = _resolve_chat_id(event)
        if not chat_id:
            errors.append("Telegram не привязан: chat id не настроен.")
        else:
            try:
                result = _send_telegram_message(text=_build_user_price_alert_message(event), chat_id=chat_id, parse_mode=HTML_PARSE_MODE)
            except Exception as exc:  # noqa: BLE001 - recorded on the event and the rule
                errors.append(str(exc))
            else:
                sent_to.append(chat_id)
                telegram_payload = result.payload
    return ", ".join(sent_to), "; ".join(errors)[:500], telegram_payload


def dispatch_pending_user_price_alerts(*, limit: int = 20) -> int:
    event_ids = list(
        UserPriceAlertEvent.objects
        .filter(delivery_status=UserPriceAlertEvent.Status.PENDING)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:limit]
    )

    sent_count = 0

    for event_id in event_ids:
        with transaction.atomic():
            event = (
                UserPriceAlertEvent.objects
                .select_related("item", "rule", "user")
                .select_for_update(skip_locked=True)
                .filter(pk=event_id, delivery_status=UserPriceAlertEvent.Status.PENDING)
                .first()
            )
            if event is None:
                continue

            sent_to, error, telegram_payload = _deliver(event)
            now = timezone.now()
            if not sent_to:
                event.delivery_status = UserPriceAlertEvent.Status.ERROR
                event.error_message = error or "Нет канала доставки."
                event.rule.last_error_at = now
                event.rule.last_error_message = event.error_message
                event.rule.save(update_fields=["last_error_at", "last_error_message", "updated_at"])
                event.save(update_fields=["delivery_status", "error_message"])
                continue

            event.delivery_status = UserPriceAlertEvent.Status.SENT
            event.sent_at = now
            event.sent_to = sent_to[:128]
            event.error_message = error
            if telegram_payload:
                event.payload = {**(event.payload or {}), "telegram_response": telegram_payload}
            event.save(update_fields=["delivery_status", "sent_at", "sent_to", "error_message", "payload"])

            event.rule.last_sent_at = now
            event.rule.last_error_message = error
            if error:
                event.rule.last_error_at = now
            event.rule.save(update_fields=["last_sent_at", "last_error_message", "last_error_at", "updated_at"])

            sent_count += 1

    return sent_count
