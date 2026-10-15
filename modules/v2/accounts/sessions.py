"""Signed-in devices ("Активные сеансы"): recording logins, activity, and ending sessions.

A UserSession row mirrors one Django session. Ending it deletes the Django session, so that device
is signed out on its next request. Activity is written at most once per TOUCH_INTERVAL per session
(remembered in the cache), so ordinary page views cost no database writes.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .devices import BOT, parse_user_agent
from .geoip import lookup
from .models import UserSession

logger = logging.getLogger(__name__)

TOUCH_INTERVAL = timedelta(minutes=5)
NEW_DEVICE_LOOKBACK = timedelta(days=90)
_CACHE_PREFIX = "user-session-touch:"


def _client_ip(request) -> str | None:
    from modules.v2.control_center.services.activity import client_ip

    return client_ip(request)


def _login_method(user) -> str:
    backend = str(getattr(user, "backend", "") or "")
    if "steam" in backend.lower():
        return UserSession.Method.STEAM
    if backend.endswith("ModelBackend"):
        return UserSession.Method.PASSWORD
    return UserSession.Method.UNKNOWN


def _session_fields(request) -> dict:
    ip = _client_ip(request)
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]
    device = parse_user_agent(user_agent)
    city, country = lookup(ip)
    return {
        "ip_address": ip,
        "last_ip": ip,
        "city": city,
        "country": country,
        "user_agent": user_agent,
        "device_type": device.device_type,
        "os": device.os,
        "browser": device.browser,
    }


def _remember_touch(session_key: str) -> None:
    cache.set(_CACHE_PREFIX + session_key, 1, int(TOUCH_INTERVAL.total_seconds()))


def register_login(request, user) -> UserSession | None:
    """Called on user_logged_in: record the new session and warn the user about an unfamiliar device."""
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    if not session_key:
        return None
    fields = _session_fields(request)
    now = timezone.now()
    record, _created = UserSession.objects.update_or_create(
        session_key=session_key,
        defaults={**fields, "user": user, "login_method": _login_method(user), "created_at": now, "last_seen_at": now, "ended_at": None, "ended_reason": ""},
    )
    _remember_touch(session_key)
    _notify_if_new_device(record)
    return record


def _notify_if_new_device(record: UserSession) -> None:
    """A login from a device type/OS/browser combination not seen recently gets a notification.

    The very first login of an account (registration) is not "new" — there is nothing to compare to.
    """
    if record.device_type == BOT:
        return
    history = UserSession.objects.filter(user_id=record.user_id, created_at__gte=timezone.now() - NEW_DEVICE_LOOKBACK).exclude(pk=record.pk)
    if not history.exists():
        return
    if history.filter(device_type=record.device_type, os=record.os, browser=record.browser).exists():
        return
    try:
        from modules.v2.notifications.services import notify

        from .notifications import KIND_NEW_LOGIN

        notify(
            record.user,
            KIND_NEW_LOGIN,
            object_id=record.pk,
            dedupe=False,
            payload={
                "device": record.device_label,
                "ip": record.ip_address or "",
                "location": record.location_label,
            },
        )
    except Exception:  # noqa: BLE001 - a notification must never block a login
        logger.exception("New-login notification failed for session %s", record.pk)


def touch(request) -> None:
    """Called by the middleware for signed-in requests: keep last_seen/IP fresh (throttled)."""
    session_key = request.session.session_key
    if not session_key or cache.get(_CACHE_PREFIX + session_key):
        return
    _remember_touch(session_key)
    record = UserSession.objects.filter(session_key=session_key).first()
    now = timezone.now()
    if record is None:
        # A session that started before session tracking existed (or after a key rotation).
        UserSession.objects.create(session_key=session_key, user=request.user, created_at=now, last_seen_at=now, **_session_fields(request))
        return
    if record.ended_at is not None or record.user_id != request.user.pk:
        return
    updates = {"last_seen_at": now}
    ip = _client_ip(request)
    if ip and ip != record.last_ip:
        city, country = lookup(ip)
        updates.update(last_ip=ip, city=city or record.city, country=country or record.country)
    UserSession.objects.filter(pk=record.pk).update(**updates)


def end_on_logout(request) -> None:
    session_key = getattr(getattr(request, "session", None), "session_key", None)
    if session_key:
        UserSession.objects.filter(session_key=session_key, ended_at__isnull=True).update(
            ended_at=timezone.now(), ended_reason=UserSession.EndReason.LOGOUT
        )


def active_sessions(user):
    """Sessions that can still be used: not ended and backed by a live Django session."""
    live = Session.objects.filter(session_key=OuterRef("session_key"), expire_date__gt=timezone.now())
    return UserSession.objects.filter(Exists(live), user=user, ended_at__isnull=True).order_by("-last_seen_at", "-id")


def recent_ended_sessions(user, *, limit: int = 10):
    return UserSession.objects.filter(user=user, ended_at__isnull=False).order_by("-ended_at", "-id")[:limit]


@transaction.atomic
def end_sessions(records, *, reason: str) -> int:
    keys = [record.session_key for record in records]
    if not keys:
        return 0
    Session.objects.filter(session_key__in=keys).delete()
    for key in keys:
        cache.delete(_CACHE_PREFIX + key)
    return UserSession.objects.filter(session_key__in=keys, ended_at__isnull=True).update(ended_at=timezone.now(), ended_reason=reason)


def end_session(user, session_id: int, *, reason: str = UserSession.EndReason.REVOKED) -> bool:
    record = UserSession.objects.filter(user=user, pk=session_id, ended_at__isnull=True).first()
    return bool(record and end_sessions([record], reason=reason))


def end_other_sessions(user, *, keep_session_key: str | None) -> int:
    records = list(UserSession.objects.filter(user=user, ended_at__isnull=True).exclude(session_key=keep_session_key or ""))
    return end_sessions(records, reason=UserSession.EndReason.REVOKED)


def end_all_sessions(user, *, reason: str = UserSession.EndReason.ADMIN) -> int:
    """Sign the user out everywhere. Also sweeps Django sessions that have no UserSession row yet
    (signed in before tracking started and not used since), so "everywhere" really is everywhere."""
    records = list(UserSession.objects.filter(user=user, ended_at__isnull=True))
    ended = end_sessions(records, reason=reason)
    tracked = {record.session_key for record in records}
    user_pk = str(user.pk)
    for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key__in=tracked).iterator():
        try:
            data = session.get_decoded()
        except Exception:  # noqa: BLE001 - corrupted sessions are skipped
            continue
        if str(data.get("_auth_user_id")) == user_pk:
            session.delete()
            ended += 1
    return ended


def prune_sessions(*, now=None) -> dict[str, int]:
    """Daily: close rows whose Django session expired, drop old history and expired Django sessions."""
    now = now or timezone.now()
    live = Session.objects.filter(session_key=OuterRef("session_key"), expire_date__gt=now)
    expired = UserSession.objects.filter(~Exists(live), ended_at__isnull=True).update(
        ended_at=now, ended_reason=UserSession.EndReason.EXPIRED
    )
    keep_days = int(getattr(settings, "USER_SESSION_RETENTION_DAYS", 90))
    deleted, _ = UserSession.objects.filter(ended_at__lt=now - timedelta(days=keep_days)).delete()
    django_deleted, _ = Session.objects.filter(expire_date__lt=now).delete()
    return {"expired": expired, "deleted": deleted, "django_sessions_deleted": django_deleted}
