"""Web push: VAPID keys, browser subscriptions, delivery and the «turn notifications on» prompt.

Pushes mirror the notification center: every new notification is also sent to the recipient's devices
(see ``signals``), grouped into categories the user can switch off. Delivery goes through the browser
vendors' push services (Google, Mozilla, Apple), so nothing extra has to be set up on the server.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from email.utils import parseaddr

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import transaction
from django.utils import timezone

from .categories import CATEGORIES, CATEGORY_TTL, DEFAULT_TTL
from .models import PushLog, PushSettings, PushSubscription, VapidKey

logger = logging.getLogger(__name__)

MAX_FAILURES = 5
PROMPT_SNOOZE_DAYS = 3
PROMPT_LONG_SNOOZE_DAYS = 30

PROMPT_REASONS = {
    "market_buy": "Продавец уже получил уведомление о покупке. Включите уведомления — сразу узнаете, когда он отправит трейд.",
    "market_listed": "Лот на витрине. Включите уведомления, чтобы не пропустить покупку: на отправку трейда даётся 24 часа.",
    "topup": "Баланс пополнен. Включите уведомления — будем сообщать о пополнениях, сделках и бонусах.",
    "daily_bonus": "Бонус получен! Включите уведомления — напомним забрать следующий, чтобы серия не сгорела.",
    "friend_request": "Заявка отправлена. Включите уведомления, чтобы узнать, когда её примут.",
    "message": "Включите уведомления, чтобы не пропустить ответ в личных сообщениях.",
}

_vapid_cache: dict[str, object] = {}


# --------------------------------------------------------------------------- keys


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def vapid():
    """The site's key pair, created on first use and kept in the database."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02

    if "vapid" not in _vapid_cache:
        row = VapidKey.objects.order_by("id").first()
        if row is None:
            key = Vapid02()
            key.generate_keys()
            pem = key.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
            public = _b64url(key.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
            row = VapidKey.objects.create(private_key=pem, public_key=public)
        _vapid_cache["vapid"] = Vapid02.from_pem(row.private_key.encode())
        _vapid_cache["public"] = row.public_key
    return _vapid_cache["vapid"]


def public_key() -> str:
    vapid()
    return _vapid_cache["public"]


def _claims_subject() -> str:
    _name, address = parseaddr(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")
    return f"mailto:{address or 'admin@case-up.ru'}"


# --------------------------------------------------------------------------- subscriptions


class PushError(ValueError):
    pass


def subscribe(user, data: dict, *, user_agent: str = "") -> PushSubscription:
    endpoint = str((data or {}).get("endpoint") or "")
    keys = (data or {}).get("keys") or {}
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not keys.get("p256dh") or not keys.get("auth"):
        raise PushError("Браузер прислал неполную подписку.")
    subscription, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={"user": user, "p256dh": str(keys["p256dh"])[:255], "auth": str(keys["auth"])[:64], "user_agent": user_agent[:300], "failures": 0},
    )
    resolve_prompt(user)
    return subscription


def unsubscribe(user, endpoint: str) -> bool:
    deleted, _ = PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
    return bool(deleted)


def settings_for(user) -> PushSettings:
    row, _ = PushSettings.objects.get_or_create(user=user)
    return row


def save_categories(user, enabled: list[str]) -> list[str]:
    row = settings_for(user)
    row.disabled_categories = [key for key in CATEGORIES if key not in set(enabled)]
    row.save(update_fields=["disabled_categories"])
    return row.disabled_categories


def settings_context(user) -> dict:
    row = PushSettings.objects.filter(user=user).first()
    disabled = set(row.disabled_categories if row else [])
    return {
        "categories": [{"key": key, "label": label, "enabled": key not in disabled} for key, label in CATEGORIES.items()],
        "devices": list(PushSubscription.objects.filter(user=user)),
    }


# --------------------------------------------------------------------------- delivery


def _payload(*, title: str, body: str = "", url: str = "", tag: str = "") -> str:
    return json.dumps(
        {
            "title": title[:120],
            "body": body[:240],
            "url": url or "/notifications/",
            "tag": tag[:64],
            "icon": staticfiles_storage.url("img/brand/icon-192.png"),
            "badge": staticfiles_storage.url("img/brand/badge-96.png"),
        },
        ensure_ascii=False,
    )


def deliver(user_id: int, *, title: str, body: str = "", url: str = "", tag: str = "", category: str = "other") -> int:
    """Sends one push to every device of the user; returns how many browsers accepted it."""
    from pywebpush import WebPushException, webpush

    disabled = PushSettings.objects.filter(user_id=user_id).values_list("disabled_categories", flat=True).first() or []
    if category in disabled:
        return 0
    data = _payload(title=title, body=body, url=url, tag=tag)
    sent = 0
    for subscription in PushSubscription.objects.filter(user_id=user_id):
        status = PushLog.Status.SENT
        try:
            webpush(
                subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                data=data,
                vapid_private_key=vapid(),
                vapid_claims={"sub": _claims_subject()},
                ttl=CATEGORY_TTL.get(category, DEFAULT_TTL),
                headers={"Urgency": "high" if category in ("market", "security", "messages") else "normal"},
                timeout=10,
            )
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                status = PushLog.Status.GONE
                subscription.delete()
            else:
                status = PushLog.Status.FAILED
                logger.warning("Push to %s failed: %s", subscription.pk, exc)
        except Exception:  # noqa: BLE001 - network trouble must not break the caller
            status = PushLog.Status.FAILED
            logger.exception("Push to %s failed", subscription.pk)
        if status == PushLog.Status.SENT:
            sent += 1
            PushSubscription.objects.filter(pk=subscription.pk).update(last_sent_at=timezone.now(), failures=0)
        elif status == PushLog.Status.FAILED:
            subscription.failures += 1
            if subscription.failures >= MAX_FAILURES:
                subscription.delete()
            else:
                PushSubscription.objects.filter(pk=subscription.pk).update(failures=subscription.failures)
        PushLog.objects.create(user_id=user_id, category=category, status=status)
    return sent


def queue(user_id: int, **push) -> None:
    """Sends after the current transaction commits, through Celery."""
    from .tasks import send_push

    if PushSubscription.objects.filter(user_id=user_id).exists():
        transaction.on_commit(lambda: send_push.delay(user_id, push))


def send_test(user) -> int:
    return deliver(user.pk, title="CaseUP: проверка уведомлений", body="Всё работает — так будут выглядеть уведомления о сделках и пополнениях.", url="/profile/settings/#settings-push", tag="test", category="test")


# --------------------------------------------------------------------------- the prompt


def request_prompt(user, reason: str) -> None:
    """Something just happened where a push would help: offer to turn notifications on (if not yet)."""
    if reason not in PROMPT_REASONS or not getattr(user, "pk", None):
        return
    try:
        if PushSubscription.objects.filter(user=user).exists():
            return
        PushSettings.objects.update_or_create(user=user, defaults={"prompt_reason": reason, "prompt_requested_at": timezone.now()})
    except Exception:  # noqa: BLE001 - a prompt must never break a purchase or a payment
        logger.exception("Could not record a push prompt for %s", user.pk)


def prompt_after_commit(user, reason: str) -> None:
    transaction.on_commit(lambda: request_prompt(user, reason))


def push_direct_message(message) -> None:
    """A new message in a direct chat: push the other member (at most once a minute per chat)."""
    from django.core.cache import cache

    from modules.v2.social.models import SocialMembership
    from modules.v2.social.services import build_space_url

    author = message.author
    recipients = SocialMembership.objects.filter(space_id=message.space_id).exclude(user_id=author.pk).values_list("user_id", flat=True)
    for user_id in recipients:
        if not cache.add(f"webpush:dm:{message.space_id}:{user_id}", 1, 60):
            continue
        queue(user_id, title=f"Сообщение от {author.username}", body=message.body, url=build_space_url(message.space), tag=f"dm:{message.space_id}", category="messages")


def prompt_for(user) -> dict | None:
    row = PushSettings.objects.filter(user=user).exclude(prompt_reason="").first()
    if row is None or (row.prompt_snoozed_until and row.prompt_snoozed_until > timezone.now()):
        return None
    if PushSubscription.objects.filter(user=user).exists():
        return None
    return {"reason": row.prompt_reason, "text": PROMPT_REASONS.get(row.prompt_reason, PROMPT_REASONS["topup"])}


def dismiss_prompt(user) -> None:
    row = settings_for(user)
    row.prompt_dismissals += 1
    days = PROMPT_LONG_SNOOZE_DAYS if row.prompt_dismissals >= 3 else PROMPT_SNOOZE_DAYS
    row.prompt_reason, row.prompt_snoozed_until = "", timezone.now() + timedelta(days=days)
    row.save(update_fields=["prompt_reason", "prompt_snoozed_until", "prompt_dismissals"])


def resolve_prompt(user) -> None:
    PushSettings.objects.filter(user=user).update(prompt_reason="")


# --------------------------------------------------------------------------- stats


def admin_summary() -> dict:
    from django.db.models import Count

    since = timezone.now() - timedelta(days=1)
    logs = dict(PushLog.objects.filter(created_at__gte=since).values_list("status").annotate(total=Count("id")))
    return {
        "users": PushSubscription.objects.values("user").distinct().count(),
        "devices": PushSubscription.objects.count(),
        "sent_day": logs.get(PushLog.Status.SENT, 0),
        "failed_day": logs.get(PushLog.Status.FAILED, 0),
        "gone_day": logs.get(PushLog.Status.GONE, 0),
        "by_category": list(
            PushLog.objects.filter(created_at__gte=since, status=PushLog.Status.SENT).values("category").annotate(total=Count("id")).order_by("-total")
        ),
        "public_key_ready": VapidKey.objects.exists(),
    }
