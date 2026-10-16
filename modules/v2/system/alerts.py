"""Admin alerts: something broke (or came back) — tell the team right away.

Channels (owner's choice 16.10.2026): the Telegram team chat (TG Notifier → «Чат по умолчанию») and a push /
notification-centre entry for every admin; e-mail to ADMINS only when Telegram could not be used.
The same alert is sent at most once per `repeat_after` seconds.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import mail_admins

logger = logging.getLogger(__name__)

ALERT_KIND = "ops_alert"
ADMIN_ROLES = ("admin", "chief_admin", "super_admin")


def _admins():
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    return get_user_model().objects.filter(Q(profile_v2__role__in=ADMIN_ROLES) | Q(is_superuser=True), is_active=True).distinct()


def _site_url(path: str) -> str:
    from modules.v2.system.services import get_public_base_url

    try:
        base = (get_public_base_url() or "").rstrip("/")
    except Exception:  # noqa: BLE001
        base = ""
    return f"{base}{path}" if path.startswith("/") else path


def _send_telegram(text: str) -> bool:
    try:
        from modules.v2.tg_notifier.services import get_telegram_notifier_config, is_enabled, resolve_bot_token, resolve_default_chat_id, send_message

        config = get_telegram_notifier_config()
        if not (is_enabled(config) and resolve_bot_token(config) and resolve_default_chat_id(config)):
            return False
        send_message(text=text, timeout=10)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin alert: telegram failed: %s", exc)
        return False


def _notify_admins(title: str, body: str, url: str) -> int:
    from modules.v2.notifications.services import notify

    sent = 0
    for admin in _admins():
        try:
            if notify(admin, ALERT_KIND, payload={"title": title, "body": body, "url": url}, dedupe=False):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("admin alert: notify %s failed: %s", admin.pk, exc)
    return sent


def send_admin_alert(key: str, title: str, text: str = "", *, url: str = "/control-center/health/", ok: bool = False, repeat_after: int = 1800) -> dict | None:
    """Sends the alert unless the same key was sent within repeat_after seconds. ok=True is a recovery
    message (always sent). Returns which channels worked, or None when skipped as a repeat."""
    if not ok and not cache.add(f"ops:alert:{key}", 1, repeat_after):
        return None
    if ok:
        cache.delete(f"ops:alert:{key}")
    icon = "✅" if ok else "🚨"
    link = _site_url(url)
    message = f"{icon} CaseUP: {title}"
    if text:
        message += f"\n{text}"
    if link:
        message += f"\n{link}"

    telegram = _send_telegram(message)
    notified = _notify_admins(f"{icon} {title}", text, url)
    mailed = False
    if not telegram:
        try:
            mail_admins(f"{icon} {title}", f"{text}\n\n{link}".strip(), fail_silently=True)
            mailed = bool(settings.ADMINS)
        except Exception:  # noqa: BLE001
            mailed = False
    return {"telegram": telegram, "notified": notified, "mail": mailed}
