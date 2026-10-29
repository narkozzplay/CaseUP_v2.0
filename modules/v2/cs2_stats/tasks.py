from __future__ import annotations

import logging
import time
from datetime import timedelta

from celery import shared_task
from django.db.models import F, Q
from django.utils import timezone


logger = logging.getLogger(__name__)

# Players viewed in the last week are refreshed every few hours, the rest once a day.
_ACTIVE_STALE_HOURS = 3
_IDLE_STALE_HOURS = 24
_BATCH_SIZE = 25
_BACKFILLS_PER_RUN = 3
_REQUEST_DELAY_SECONDS = 0.5


@shared_task(name="cs2_stats.refresh_tracked_players", max_retries=0)
def refresh_tracked_players_task() -> dict:
    from .models import TrackedPlayer
    from .players import backfill_player, enroll_caseup_users, needs_backfill, refresh_player

    now = timezone.now()
    enrolled = enroll_caseup_users()
    due = (
        TrackedPlayer.objects.filter(
            Q(last_synced_at__isnull=True)
            | Q(last_viewed_at__gte=now - timedelta(days=7), last_synced_at__lt=now - timedelta(hours=_ACTIVE_STALE_HOURS))
            | Q(last_synced_at__lt=now - timedelta(hours=_IDLE_STALE_HOURS))
        )
        .order_by(F("last_synced_at").asc(nulls_first=True))[:_BATCH_SIZE]
    )
    refreshed = errors = 0
    for player in due:
        try:
            refresh_player(player)
            refreshed += 1
        except Exception as exc:
            errors += 1
            # Stamp the attempt so a broken profile does not block the queue.
            TrackedPlayer.objects.filter(pk=player.pk).update(last_synced_at=timezone.now())
            logger.info("refresh tracked player %s: %s", player.steam_id, exc)
        time.sleep(_REQUEST_DELAY_SECONDS)

    backfilled = 0
    pending = TrackedPlayer.objects.exclude(faceit_player_id="").filter(
        Q(faceit_history_complete=False)
        | Q(faceit_elo_history_checked_at__isnull=True)
        | Q(faceit_elo_history_checked_at__lt=now - timedelta(days=7))
    ).order_by("-last_viewed_at")[:_BACKFILLS_PER_RUN * 3]
    for player in pending:
        if backfilled >= _BACKFILLS_PER_RUN:
            break
        if not needs_backfill(player):
            continue
        try:
            backfill_player(player)
            backfilled += 1
        except Exception as exc:
            logger.info("backfill tracked player %s: %s", player.steam_id, exc)

    return {"enrolled": enrolled, "refreshed": refreshed, "errors": errors, "backfilled": backfilled}


@shared_task(name="cs2_stats.backfill_player", max_retries=0)
def backfill_player_task(player_id: int) -> dict:
    from .models import TrackedPlayer
    from .players import backfill_player

    player = TrackedPlayer.objects.filter(pk=player_id).first()
    if player is None:
        return {"skipped": "missing"}
    try:
        return backfill_player(player)
    except Exception as exc:
        logger.info("backfill player %s: %s", player.steam_id, exc)
        return {"error": str(exc)[:200]}
