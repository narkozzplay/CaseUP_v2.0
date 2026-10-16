from __future__ import annotations

import platform
import shutil
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import django
from django.conf import settings
from django.core.cache import cache
from django.db import DEFAULT_DB_ALIAS, connections
from django.utils import timezone

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"


@dataclass(frozen=True)
class HealthCheck:
    key: str
    label: str
    status: str
    value: str
    hint: str = ""


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def format_ms(value: float) -> str:
    if value < 0.1:
        return "<0.1 мс"
    if value < 10:
        return f"{value:.1f} мс"
    return f"{value:.0f} мс"


def format_bytes(value: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def database_size_bytes() -> int | None:
    connection = connections[DEFAULT_DB_ALIAS]
    try:
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database())")
                return int(cursor.fetchone()[0])
        if connection.vendor == "sqlite":
            name = str(connection.settings_dict.get("NAME") or "")
            return Path(name).stat().st_size if name and name != ":memory:" else None
    except Exception:  # noqa: BLE001
        return None
    return None


MEDIA_SCAN_LIMIT = 20000


def media_size_bytes() -> tuple[int, bool]:
    root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    total = 0
    scanned = 0
    if not root.is_dir():
        return 0, False
    for path in root.rglob("*"):
        if scanned >= MEDIA_SCAN_LIMIT:
            return total, True
        try:
            if path.is_file():
                total += path.stat().st_size
                scanned += 1
        except OSError:
            continue
    return total, False


def check_database() -> HealthCheck:
    started = time.perf_counter()
    try:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("database", "База данных", FAIL, "Нет соединения", exc.__class__.__name__)
    latency = _elapsed_ms(started)
    vendor = connections[DEFAULT_DB_ALIAS].vendor
    size = database_size_bytes()
    hint = f"{vendor} · {format_bytes(size)}" if size is not None else vendor
    return HealthCheck("database", "База данных", OK if latency < 200 else WARN, format_ms(latency), hint)


def check_migrations() -> HealthCheck:
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("migrations", "Миграции", WARN, "Не проверено", exc.__class__.__name__)
    if pending:
        return HealthCheck("migrations", "Миграции", FAIL, f"{len(pending)} не применено", "python manage.py migrate")
    return HealthCheck("migrations", "Миграции", OK, "Все применены")


def check_cache() -> HealthCheck:
    started = time.perf_counter()
    key = "control_center:health:ping"
    try:
        cache.set(key, "pong", 10)
        value = cache.get(key)
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("cache", "Кэш", FAIL, "Ошибка", exc.__class__.__name__)
    latency = _elapsed_ms(started)
    backend = settings.CACHES.get("default", {}).get("BACKEND", "").rsplit(".", 1)[-1]
    if value != "pong":
        return HealthCheck("cache", "Кэш", WARN, "Не сохраняет", backend)
    if backend == "LocMemCache":
        return HealthCheck("cache", "Кэш", OK, format_ms(latency), "LocMemCache · память процесса")
    return HealthCheck("cache", "Кэш", OK, format_ms(latency), backend)


def check_celery_broker() -> HealthCheck:
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return HealthCheck("celery_broker", "Celery broker", INFO, "Eager-режим", "Задачи выполняются синхронно")
    try:
        from config.celery import app

        with app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0, timeout=2)
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("celery_broker", "Celery broker", FAIL, "Недоступен", exc.__class__.__name__)
    return HealthCheck("celery_broker", "Celery broker", OK, "Подключен", settings.CELERY_BROKER_URL.split("://", 1)[0])


def check_celery_workers() -> HealthCheck:
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return HealthCheck("celery_workers", "Celery workers", INFO, "Eager-режим")
    try:
        from config.celery import app

        replies = app.control.inspect(timeout=1.0).ping() or {}
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("celery_workers", "Celery workers", FAIL, "Нет ответа", exc.__class__.__name__)
    if not replies:
        return HealthCheck("celery_workers", "Celery workers", FAIL, "0 онлайн", "Запустите worker")
    return HealthCheck("celery_workers", "Celery workers", OK, f"{len(replies)} онлайн", ", ".join(sorted(replies))[:120])


def check_price_updates() -> HealthCheck:
    try:
        from modules.v2.celery_tasks.models import PriceAutoUpdateRule, PriceAutoUpdateRunLog
    except Exception:  # noqa: BLE001
        return HealthCheck("price_updates", "Авто-обновление цен", INFO, "Модуль недоступен")
    last = PriceAutoUpdateRunLog.objects.order_by("-started_at").first()
    if last is None:
        return HealthCheck("price_updates", "Авто-обновление цен", INFO, "Запусков ещё не было")
    failed_day = PriceAutoUpdateRunLog.objects.filter(
        started_at__gte=timezone.now() - timedelta(hours=24),
        status=PriceAutoUpdateRule.Status.ERROR,
    ).count()
    age = timezone.now() - last.started_at
    status = OK
    if failed_day:
        status = WARN
    if age > timedelta(days=1):
        status = WARN
    hint = f"Ошибок за 24 ч: {failed_day}" if failed_day else f"Статус: {last.status}"
    return HealthCheck("price_updates", "Авто-обновление цен", status, timezone.localtime(last.started_at).strftime("%d.%m %H:%M"), hint)


def check_disk(*, include_media: bool = False) -> HealthCheck:
    target = Path(getattr(settings, "MEDIA_ROOT", "") or settings.BASE_DIR)
    if not target.exists():
        target = Path(settings.BASE_DIR)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return HealthCheck("disk", "Диск", WARN, "Не проверено", exc.__class__.__name__)
    used_pct = usage.used * 100 / usage.total if usage.total else 0
    status = OK if used_pct < 85 else (WARN if used_pct < 95 else FAIL)
    pct_label = f"{used_pct:.1f}" if used_pct < 10 else f"{used_pct:.0f}"
    hint = f"Занято {pct_label}% из {format_bytes(usage.total)}"
    if include_media:
        media, truncated = media_size_bytes()
        hint += f" · media {'≥' if truncated else ''}{format_bytes(media)}"
    return HealthCheck("disk", "Диск", status, f"{format_bytes(usage.free)} свободно", hint)


def check_backups() -> HealthCheck:
    from modules.v2.system.backups import status_summary

    try:
        summary = status_summary()
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("backups", "Резервные копии", WARN, "Не проверено", exc.__class__.__name__)
    state = {"ok": OK, "warn": WARN, "fail": FAIL}.get(summary["state"], WARN)
    finished = summary.get("finished")
    value = timezone.localtime(finished).strftime("%d.%m %H:%M") if finished else "—"
    return HealthCheck("backups", "Резервные копии", state, value, summary["text"][:160])


def check_email() -> HealthCheck:
    host = getattr(settings, "EMAIL_HOST", "")
    admins = getattr(settings, "ADMINS", [])
    if not host:
        return HealthCheck("email", "Почта", WARN, "Не настроена", "Письма об ошибках и сбоях бэкапа не отправляются: заполните EMAIL_* в .env.prod")
    if not admins:
        return HealthCheck("email", "Почта", WARN, host, "Не задан ADMIN_EMAILS — некому слать письма об ошибках")
    return HealthCheck("email", "Почта", OK, host, "Ошибки сайта и сбои бэкапа: " + ", ".join(email for _name, email in admins)[:120])


def check_scheduler() -> HealthCheck:
    """The watchdog task runs every 2 minutes: a fresh heartbeat means beat schedules and a worker executes."""
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return HealthCheck("scheduler", "Планировщик задач", INFO, "Eager-режим")
    from modules.v2.system.watchdog import HEARTBEAT_STALE_SECONDS, heartbeat_age_seconds

    age = heartbeat_age_seconds()
    if age is None:
        return HealthCheck("scheduler", "Планировщик задач", FAIL, "Нет пульса", "Задача system.watchdog ни разу не выполнялась: проверьте celery_beat и celery_worker")
    label = f"{int(age)} с назад" if age < 120 else f"{int(age // 60)} мин назад"
    if age > HEARTBEAT_STALE_SECONDS:
        return HealthCheck("scheduler", "Планировщик задач", FAIL, label, "Фоновые задачи стоят: dc ps, dc logs --tail=100 celery_beat celery_worker")
    return HealthCheck("scheduler", "Планировщик задач", OK, label, "Пульс каждые 2 минуты")


def check_errors() -> HealthCheck:
    from modules.v2.system.models import ErrorGroup

    open_groups = ErrorGroup.objects.filter(resolved_at__isnull=True)
    day = open_groups.filter(last_seen__gte=timezone.now() - timedelta(hours=24)).count()
    total = open_groups.count()
    if not total:
        return HealthCheck("errors", "Ошибки", OK, "Нет открытых")
    return HealthCheck("errors", "Ошибки", WARN if day else OK, f"{total} открыто", f"За сутки: {day}. Control Center → «Ошибки»")


def read_build_version() -> str:
    path = Path(settings.BASE_DIR) / "VERSION.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "—"


def runtime_info() -> list[tuple[str, str]]:
    from modules.v2.system.services import get_runtime_environment

    return [
        ("Build", read_build_version()),
        ("Environment", get_runtime_environment()),
        ("Django", django.get_version()),
        ("Python", platform.python_version()),
        ("DEBUG", "on" if settings.DEBUG else "off"),
        ("Timezone", settings.TIME_ZONE),
        ("Settings", (getattr(settings, "SETTINGS_MODULE", "") or "").rsplit(".", 1)[-1]),
    ]


def quick_checks() -> list[HealthCheck]:
    return [check_database(), check_cache(), check_price_updates(), check_disk()]


def full_checks() -> list[HealthCheck]:
    checks = [check_database(), check_migrations(), check_cache(), check_celery_broker()]
    if checks[-1].status != FAIL:
        checks.append(check_celery_workers())
    checks.extend([check_scheduler(), check_errors(), check_price_updates(), check_disk(include_media=True), check_backups(), check_email()])
    return checks


def overall_status(checks: list[HealthCheck]) -> str:
    statuses = {check.status for check in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK
