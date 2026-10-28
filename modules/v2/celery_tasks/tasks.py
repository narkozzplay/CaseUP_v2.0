from __future__ import annotations

import logging
import time
import uuid

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.utils import timezone

from modules.v2.steam_catalog.services import dispatch_pending_price_drop_alerts
from modules.v2.steam_trading.services import dispatch_due_unlock_notifications

from .services import run_due_rules, run_price_auto_update_rule, scheduler_deadline, task_deadline


logger = logging.getLogger(__name__)

User = get_user_model()

PRICE_RUN_LOCK_KEY = "celery_tasks:price_auto_updates:running"

# Max users to process per single task run (rate-limit safety)
_CS2_BATCH_SIZE = 50
# Only sync users whose stats are older than this many minutes
_CS2_STALE_AFTER_MINUTES = 25
# Pause between API calls to avoid hitting Steam / FACEIT rate limits
_CS2_REQUEST_DELAY_SECONDS = 0.4


@shared_task(name="celery_tasks.run_price_auto_update_rule")
def run_price_auto_update_rule_task(rule_id: int, triggered_by: str = "manual") -> dict:
    summary = run_price_auto_update_rule(rule_id, triggered_by=triggered_by, deadline=task_deadline())
    return {
        "rule_id": summary.rule_id,
        "category": summary.category,
        "status": summary.status,
        "touched_items_count": summary.touched_items_count,
        "updated_items_count": summary.updated_items_count,
        "history_written_count": summary.history_written_count,
        "errors_count": summary.errors_count,
    }


@shared_task(name="celery_tasks.run_due_price_auto_updates")
def run_due_price_auto_updates_task() -> list[dict]:
    # A run still busy (slow alert detection, a stuck worker) is not joined by a second one: they would share the
    # same Steam slots anyway and only mark each other's rules as «previous run is still active».
    # The lease outlives the task's hard time limit, so it cannot expire under a run that is still going; only its
    # owner releases it.
    lock_seconds = int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 1200) or 1200) + 60
    token = uuid.uuid4().hex
    if not cache.add(PRICE_RUN_LOCK_KEY, token, timeout=lock_seconds):
        return []
    try:
        summaries = run_due_rules(triggered_by="beat")
    finally:
        if cache.get(PRICE_RUN_LOCK_KEY) == token:
            cache.delete(PRICE_RUN_LOCK_KEY)
    return [
        {
            "rule_id": summary.rule_id,
            "category": summary.category,
            "status": summary.status,
            "touched_items_count": summary.touched_items_count,
            "updated_items_count": summary.updated_items_count,
            "history_written_count": summary.history_written_count,
            "errors_count": summary.errors_count,
        }
        for summary in summaries
    ]


SWEEP_LOCK_KEY = "celery_tasks:price_sweep:running"


@shared_task(name="celery_tasks.sweep_market_prices")
def sweep_market_prices_task() -> dict:
    from modules.v2.steam_catalog.services.price_sweep import sweep

    lock_seconds = int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 1200) or 1200) + 60
    token = uuid.uuid4().hex
    if not cache.add(SWEEP_LOCK_KEY, token, timeout=lock_seconds):
        return {}
    try:
        result = sweep(deadline=scheduler_deadline())
    finally:
        if cache.get(SWEEP_LOCK_KEY) == token:
            cache.delete(SWEEP_LOCK_KEY)
    for line in result.errors:
        logger.warning("Market sweep: %s", line)
    return {
        "pages": result.pages,
        "matched": result.matched,
        "updated": result.updated,
        "history_written": result.history_written,
        "errors": result.errors,
        "notes": result.notes,
    }


@shared_task(name="celery_tasks.dispatch_pending_price_drop_alerts")
def dispatch_pending_price_drop_alerts_task(limit: int = 20) -> dict:
    try:
        sent_count = dispatch_pending_price_drop_alerts(limit=limit)
    except (ProgrammingError, OperationalError) as exc:
        logger.warning(
            "Skipping dispatch_pending_price_drop_alerts_task because database schema is not ready: %s",
            exc,
        )
        return {
            "sent_count": 0,
            "limit": limit,
            "skipped": True,
            "reason": "database_schema_not_ready",
        }

    return {
        "sent_count": sent_count,
        "limit": limit,
    }


@shared_task(name="celery_tasks.auto_sync_cs2_steam_stats", max_retries=0)
def auto_sync_cs2_steam_stats_task() -> dict:
    """Auto-update CS2 Steam stats for all users with linked Steam ID.

    Skips users synced within the last _CS2_STALE_AFTER_MINUTES minutes.
    Processes at most _CS2_BATCH_SIZE users per run (ordered by oldest sync first).
    """
    from datetime import timedelta

    from modules.v2.cs2_stats.services import sync_cs2_steam_stats
    from modules.v2.profiles.models import UserProfile

    try:
        stale_threshold = timezone.now() - timedelta(minutes=_CS2_STALE_AFTER_MINUTES)

        # Users with non-empty steam_id
        user_ids_with_steam = (
            UserProfile.objects
            .filter(steam_id__isnull=False)
            .exclude(steam_id="")
            .values_list("user_id", flat=True)
        )

        # Among them — those with no stats OR stale stats
        users_qs = (
            User.objects
            .filter(id__in=user_ids_with_steam)
            .filter(
                Q(cs2_player_stats__isnull=True)
                | Q(cs2_player_stats__last_synced_at__isnull=True)
                | Q(cs2_player_stats__last_synced_at__lt=stale_threshold)
            )
            .distinct()
            .order_by("cs2_player_stats__last_synced_at")  # oldest first
        )[:_CS2_BATCH_SIZE]

    except (OperationalError, ProgrammingError) as exc:
        logger.warning("auto_sync_cs2_steam_stats: DB not ready — %s", exc)
        return {"synced": 0, "skipped": 0, "errors": 0, "skipped_reason": "db_not_ready"}

    synced = skipped = errors = 0

    for user in users_qs:
        try:
            result = sync_cs2_steam_stats(user)
            if result.ok:
                synced += 1
                logger.debug("auto_sync_cs2_steam_stats: synced user=%s", user.id)
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.error("auto_sync_cs2_steam_stats: user=%s error=%s", user.id, exc)

        time.sleep(_CS2_REQUEST_DELAY_SECONDS)

    logger.info(
        "auto_sync_cs2_steam_stats done: synced=%d skipped=%d errors=%d",
        synced, skipped, errors,
    )
    return {"synced": synced, "skipped": skipped, "errors": errors}


@shared_task(name="celery_tasks.auto_sync_cs2_faceit_stats", max_retries=0)
def auto_sync_cs2_faceit_stats_task() -> dict:
    """Auto-update FACEIT CS2 stats for all users with linked FACEIT account.

    Same batching and stale-threshold logic as the Steam task.
    """
    from datetime import timedelta

    from modules.v2.cs2_stats.services import sync_faceit_extended_stats
    from modules.v2.profiles.models import UserProfile

    try:
        stale_threshold = timezone.now() - timedelta(minutes=_CS2_STALE_AFTER_MINUTES)

        user_ids_with_faceit = (
            UserProfile.objects
            .filter(faceit_player_id__isnull=False)
            .exclude(faceit_player_id="")
            .values_list("user_id", flat=True)
        )

        users_qs = (
            User.objects
            .filter(id__in=user_ids_with_faceit)
            .filter(
                Q(faceit_extended_stats__isnull=True)
                | Q(faceit_extended_stats__last_synced_at__isnull=True)
                | Q(faceit_extended_stats__last_synced_at__lt=stale_threshold)
            )
            .distinct()
            .order_by("faceit_extended_stats__last_synced_at")  # oldest first
        )[:_CS2_BATCH_SIZE]

    except (OperationalError, ProgrammingError) as exc:
        logger.warning("auto_sync_cs2_faceit_stats: DB not ready — %s", exc)
        return {"synced": 0, "skipped": 0, "errors": 0, "skipped_reason": "db_not_ready"}

    synced = skipped = errors = 0

    for user in users_qs:
        try:
            result = sync_faceit_extended_stats(user)
            if result.ok:
                synced += 1
                logger.debug("auto_sync_cs2_faceit_stats: synced user=%s", user.id)
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.error("auto_sync_cs2_faceit_stats: user=%s error=%s", user.id, exc)

        time.sleep(_CS2_REQUEST_DELAY_SECONDS)

    logger.info(
        "auto_sync_cs2_faceit_stats done: synced=%d skipped=%d errors=%d",
        synced, skipped, errors,
    )
    return {"synced": synced, "skipped": skipped, "errors": errors}


@shared_task(name="celery_tasks.dispatch_due_steam_trade_unlocks")
def dispatch_due_steam_trade_unlocks_task(limit: int = 20) -> dict:
    summary = dispatch_due_unlock_notifications(limit=limit)
    return {
        "processed_count": summary.processed_count,
        "sent_count": summary.sent_count,
        "error_count": summary.error_count,
        "limit": limit,
    }