"""Watchdog (Celery beat, every 2 minutes): heartbeat + admin alerts when something breaks or recovers.

- The run itself is the heartbeat: it proves that beat schedules and a worker executes. /health/ready/ and
  Control Center → «Система» report it; an external monitor on /health/ready/ catches a dead scheduler,
  which cannot alert about itself.
- Health checks from Control Center → «Система»: an alert when one turns bad, a reminder while it stays bad,
  a «снова в норме» message when it recovers.
- New errors in the error journal: one combined alert per run.
- Telegram poller («Опрос» mode): no polls for 5 minutes or poll errors for TG_POLL_FAILING_MINUTES in a row.
  Single 502s / dropped connections of Telegram or the relay are normal and recover by themselves, so they are
  not reported; after an alert «снова в норме» comes only once polls have been clean for TG_POLL_CLEAN_MINUTES.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from .alerts import send_admin_alert

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "ops:heartbeat"
HEARTBEAT_STALE_SECONDS = 10 * 60
STATE_KEY = "ops:watch:{key}"
REMIND_SECONDS = 6 * 3600
TG_POLL_STALE_MINUTES = 5
TG_POLL_FAILING_MINUTES = 15
TG_POLL_CLEAN_MINUTES = 15

# check key → statuses that count as broken. WARN of the database (slow query) or e-mail (not configured)
# is not worth waking anybody up.
WATCHED = {
    "database": {"fail"},
    "migrations": {"fail"},
    "cache": {"fail"},
    "celery_broker": {"fail"},
    "price_updates": {"warn", "fail"},
    "disk": {"warn", "fail"},
    "backups": {"warn", "fail"},
}


def beat() -> None:
    cache.set(HEARTBEAT_KEY, timezone.now(), 24 * 3600)


def last_heartbeat():
    return cache.get(HEARTBEAT_KEY)


def heartbeat_age_seconds() -> float | None:
    last = last_heartbeat()
    return (timezone.now() - last).total_seconds() if last else None


def _transition(key: str, label: str, broken: bool, detail: str, url: str) -> str:
    """Sends the alert for a state change; returns "alert", "recovered", "reminder" or ""."""
    state_key = STATE_KEY.format(key=key)
    was_broken = bool(cache.get(state_key))
    if broken:
        cache.set(state_key, 1, 7 * 24 * 3600)
        sent = send_admin_alert(f"watch:{key}", f"{label}: проблема", detail, url=url, repeat_after=REMIND_SECONDS)
        if sent is None:
            return ""
        return "reminder" if was_broken else "alert"
    if was_broken:
        cache.delete(state_key)
        send_admin_alert(f"watch:{key}", f"{label}: снова в норме", detail, url=url, ok=True)
        return "recovered"
    return ""


def _health_checks():
    """The checks of Control Center → «Система» that can break, without the slow ones (media size scan,
    worker ping: the watchdog running at all already proves a worker is alive)."""
    from modules.v2.control_center.services import health

    return [
        health.check_database(),
        health.check_migrations(),
        health.check_cache(),
        health.check_celery_broker(),
        health.check_price_updates(),
        health.check_disk(),
        health.check_backups(),
    ]


def check_health() -> dict[str, str]:
    results = {}
    for check in _health_checks():
        broken_statuses = WATCHED.get(check.key)
        if broken_statuses is None:
            continue
        detail = " · ".join(part for part in (check.value, check.hint) if part)
        outcome = _transition(check.key, check.label, check.status in broken_statuses, detail, "/control-center/health/")
        if outcome:
            results[check.key] = outcome
    return results


def check_new_errors() -> int:
    from .models import ErrorGroup

    fresh = list(ErrorGroup.objects.filter(resolved_at__isnull=True, alerted_at__isnull=True).order_by("-last_seen")[:20])
    if not fresh:
        return 0
    lines = [f"• {group.title[:120]} ({group.get_source_display().lower()}, ×{group.count})" for group in fresh[:5]]
    if len(fresh) > 5:
        lines.append(f"…и ещё {len(fresh) - 5}")
    title = "Новая ошибка" if len(fresh) == 1 else f"Новые ошибки: {len(fresh)}"
    # Error alerts are not throttled by key: each group is announced once (alerted_at), a resolved one again
    # only if it comes back.
    send_admin_alert(f"errors:{fresh[0].pk}:{timezone.now().timestamp()}", title, "\n".join(lines), url="/control-center/errors/")
    ErrorGroup.objects.filter(pk__in=[group.pk for group in fresh]).update(alerted_at=timezone.now())
    return len(fresh)


def check_telegram_poller() -> str:
    try:
        from modules.v2.tg_notifier.models import TelegramNotifierConfig
        from modules.v2.tg_notifier.services import get_telegram_notifier_config, is_enabled, resolve_bot_token
        from modules.v2.tg_notifier.updates import get_poll_status
    except Exception:  # noqa: BLE001
        return ""
    label, url = "Приём сообщений Telegram", "/control-center/tg-notifier/"
    config = get_telegram_notifier_config()
    if config.updates_mode != TelegramNotifierConfig.UpdatesMode.POLLING or not is_enabled(config) or not resolve_bot_token(config):
        return _transition("tg_poller", label, False, "", url)
    status = get_poll_status()
    now = timezone.now()
    if not status:
        return _transition("tg_poller", label, True, "Сборщик telegram_poller ни разу не опрашивал Telegram — контейнер запущен?", url)
    if (now - status["at"]) > timedelta(minutes=TG_POLL_STALE_MINUTES):
        return _transition("tg_poller", label, True, f"Последний опрос {timezone.localtime(status['at']):%d.%m %H:%M} — сборщик остановлен.", url)

    failing_since = status.get("failing_since")
    if status.get("error") and failing_since and (now - failing_since) >= timedelta(minutes=TG_POLL_FAILING_MINUTES):
        minutes = int((now - failing_since).total_seconds() // 60)
        return _transition("tg_poller", label, True, f"Опросы не проходят {minutes} мин. Последняя ошибка: {status['error'][:200]}", url)

    last_error_at = status.get("last_error_at")
    flapping = bool(last_error_at) and (now - last_error_at) < timedelta(minutes=TG_POLL_CLEAN_MINUTES)
    if flapping and cache.get(STATE_KEY.format(key="tg_poller")):
        # Already reported and still unstable: neither a new alert nor a premature «снова в норме».
        return ""
    return _transition("tg_poller", label, False, "Опросы идут.", url)


def run_watchdog() -> dict:
    beat()
    report: dict = {}
    for name, step in (("health", check_health), ("errors", check_new_errors), ("tg_poller", check_telegram_poller)):
        try:
            report[name] = step()
        except Exception:  # noqa: BLE001 - one broken check must not hide the others
            logger.exception("watchdog: %s failed", name)
            report[name] = "error"
    return report
