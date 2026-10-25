"""Notification kinds of the support service."""
from __future__ import annotations

from modules.v2.notifications.registry import payload_renderer, register_kind

KIND_REPLY = "support_reply"
KIND_STATUS = "support_status"
KIND_STAFF = "support_staff"
KIND_NEW = "support_new"

# Kinds the author of a ticket gets; they are marked read once the ticket is opened.
USER_KINDS = (KIND_REPLY, KIND_STATUS)


def register_notification_kinds() -> None:
    for kind in (KIND_REPLY, KIND_STATUS, KIND_STAFF, KIND_NEW):
        register_kind(kind, payload_renderer("support"))
