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


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def check_database() -> HealthCheck:
    started = time.perf_counter()
    try:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("database", "База данных", FAIL, "Нет соединения", exc.__class__.__name__)
    latency = _ms(started)
    vendor = connections[DEFAULT_DB_ALIAS].vendor
    return HealthCheck("database", "База данных", OK if latency < 200 else WARN, f"{latency} мс", vendor)


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
    backend = settings.CACHES.get("default", {}).get("BACKEND", "").rsplit(".", 1)[-1]
    if value != "pong":
        return HealthCheck("cache", "Кэш", WARN, "Не сохраняет", backend)
    return HealthCheck("cache", "Кэш", OK, f"{_ms(started)} мс", backend)


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


def check_disk() -> HealthCheck:
    target = Path(getattr(settings, "MEDIA_ROOT", "") or settings.BASE_DIR)
    if not target.exists():
        target = Path(settings.BASE_DIR)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return HealthCheck("disk", "Диск", WARN, "Не проверено", exc.__class__.__name__)
    free_gb = usage.free / 1024**3
    used_pct = round(usage.used * 100 / usage.total) if usage.total else 0
    status = OK if used_pct < 85 else (WARN if used_pct < 95 else FAIL)
    return HealthCheck("disk", "Диск", status, f"{free_gb:.1f} ГБ свободно", f"Занято {used_pct}%")


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
    checks.extend([check_price_updates(), check_disk()])
    return checks


def overall_status(checks: list[HealthCheck]) -> str:
    statuses = {check.status for check in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK
