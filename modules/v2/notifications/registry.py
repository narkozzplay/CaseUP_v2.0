"""Registry of notification kinds.

Each app that emits notifications registers a renderer for its kinds, usually
from ``AppConfig.ready()``::

    from modules.v2.notifications.registry import register_kind

    register_kind("friend_request", render_friend_request)

A renderer receives the :class:`Notification` and the active language code
("ru" or "en") and returns a :class:`NotificationView`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class NotificationAction:
    """A POST action shown as a button next to the notification."""

    label: str
    url: str
    style: str = "secondary"  # "primary" | "secondary" | "ghost"


@dataclass(frozen=True)
class NotificationView:
    title: str
    body: str = ""
    url: str = ""
    icon: str = "notification_center"
    actor_avatar_url: str = ""
    actions: tuple[NotificationAction, ...] = field(default_factory=tuple)
    state_label: str = ""


Renderer = Callable[[object, str], NotificationView]

_RENDERERS: dict[str, Renderer] = {}


def register_kind(kind: str, renderer: Renderer) -> None:
    _RENDERERS[kind] = renderer


def get_renderer(kind: str) -> Renderer | None:
    return _RENDERERS.get(kind)


def registered_kinds() -> tuple[str, ...]:
    return tuple(sorted(_RENDERERS))
