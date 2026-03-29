from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.steam_catalog.models import PriceDropAlertEvent, PriceDropAlertRule

from .alerts import _build_price_drop_alert_message
from .user_alerts import dispatch_pending_user_price_alerts


HTML_PARSE_MODE = "HTML"


def _telegram_send_message(
    *,
    text: str,
    chat_id: str | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    from modules.v2.tg_notifier.services import send_message

    result = send_message(text=text, chat_id=chat_id, parse_mode=parse_mode)
    return result.payload


def _get_price_alert_recipient_chat_ids() -> list[str]:
    chat_ids = list(
        UserProfile.objects
        .exclude(telegram_chat_id__isnull=True)
        .exclude(telegram_chat_id="")
        .order_by()
        .values_list("telegram_chat_id", flat=True)
        .distinct()
    )

    if chat_ids:
        return chat_ids

    from modules.v2.tg_notifier.services import resolve_default_chat_id

    default_chat_id = (resolve_default_chat_id() or "").strip()
    if default_chat_id:
        return [default_chat_id]

    return []


def dispatch_pending_price_drop_alerts(*, limit: int = 20) -> int:
    pending_event_ids = list(
        PriceDropAlertEvent.objects
        .filter(
            status=PriceDropAlertEvent.Status.PENDING,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:limit]
    )

    sent_count = 0

    for event_id in pending_event_ids:
        with transaction.atomic():
            event = (
                PriceDropAlertEvent.objects
                .select_related("item", "rule")
                .select_for_update(skip_locked=True)
                .filter(
                    pk=event_id,
                    status=PriceDropAlertEvent.Status.PENDING,
                    notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
                )
                .first()
            )
            if event is None:
                continue

            recipient_chat_ids = _get_price_alert_recipient_chat_ids()
            if not recipient_chat_ids:
                event.status = PriceDropAlertEvent.Status.ERROR
                event.error_message = "No linked Telegram recipients and no default chat id configured."
                event.payload = {
                    **(event.payload or {}),
                    "delivery_error": "no_recipients",
                    "recipient_count": 0,
                    "sent_chat_ids": [],
                    "failed_chat_ids": [],
                }
                event.save(update_fields=["status", "error_message", "payload"])
                continue

            sent_chat_ids: list[str] = []
            failed_chat_ids: list[dict[str, str]] = []
            telegram_responses: list[dict[str, Any]] = []
            message_text = _build_price_drop_alert_message(event)

            for chat_id in recipient_chat_ids:
                try:
                    response_data = _telegram_send_message(
                        text=message_text,
                        chat_id=chat_id,
                        parse_mode=HTML_PARSE_MODE,
                    )
                except Exception as exc:
                    failed_chat_ids.append(
                        {
                            "chat_id": chat_id,
                            "error": str(exc)[:500],
                        }
                    )
                    continue

                sent_chat_ids.append(chat_id)
                telegram_responses.append(
                    {
                        "chat_id": chat_id,
                        "response": response_data,
                    }
                )

            payload = {
                **(event.payload or {}),
                "recipient_count": len(recipient_chat_ids),
                "sent_chat_ids": sent_chat_ids,
                "failed_chat_ids": failed_chat_ids,
                "telegram_responses": telegram_responses,
            }

            if telegram_responses:
                payload["telegram_response"] = telegram_responses[0]["response"]

            if not sent_chat_ids:
                event.status = PriceDropAlertEvent.Status.ERROR
                event.error_message = "Telegram delivery failed for all recipients."
                event.payload = payload
                event.save(update_fields=["status", "error_message", "payload"])
                continue

            event.status = PriceDropAlertEvent.Status.SENT
            event.sent_at = timezone.now()
            event.error_message = (
                f"Partial delivery error. Failed recipients: {len(failed_chat_ids)}"
                if failed_chat_ids
                else ""
            )
            event.payload = payload
            event.save(update_fields=["status", "sent_at", "error_message", "payload"])
            sent_count += 1

    sent_count += dispatch_pending_user_price_alerts(limit=limit)
    return sent_count


__all__ = ["_telegram_send_message", "dispatch_pending_price_drop_alerts"]