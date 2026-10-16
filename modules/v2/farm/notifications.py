"""Site notifications (and pushes) that used to go only to Telegram."""
from __future__ import annotations

from modules.v2.notifications.registry import payload_renderer, register_kind

KIND_FARM_ALERT = "farm_alert"


def register_notification_kinds() -> None:
    register_kind(KIND_FARM_ALERT, payload_renderer("farm_hub"))
