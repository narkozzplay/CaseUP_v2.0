"""Site notifications (and pushes) that used to go only to Telegram."""
from __future__ import annotations

from modules.v2.notifications.registry import payload_renderer, register_kind

KIND_PRICE_ALERT = "steam_price_alert"


def register_notification_kinds() -> None:
    register_kind(KIND_PRICE_ALERT, payload_renderer("steam_catalog"))
