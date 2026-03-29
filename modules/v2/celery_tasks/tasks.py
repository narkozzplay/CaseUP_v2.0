from __future__ import annotations

from celery import shared_task

from modules.v2.steam_catalog.services import dispatch_pending_price_drop_alerts
from modules.v2.steam_trading.services import dispatch_due_unlock_notifications

from .services import run_due_rules, run_price_auto_update_rule


@shared_task(name="celery_tasks.run_price_auto_update_rule")
def run_price_auto_update_rule_task(rule_id: int, triggered_by: str = "manual") -> dict:
    summary = run_price_auto_update_rule(rule_id, triggered_by=triggered_by)
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
    summaries = run_due_rules(triggered_by="beat")
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


@shared_task(name="celery_tasks.dispatch_pending_price_drop_alerts")
def dispatch_pending_price_drop_alerts_task(limit: int = 20) -> dict:
    sent_count = dispatch_pending_price_drop_alerts(limit=limit)
    return {
        "sent_count": sent_count,
        "limit": limit,
    }

@shared_task(name="celery_tasks.dispatch_due_steam_trade_unlocks")
def dispatch_due_steam_trade_unlocks_task(limit: int = 20) -> dict:
    summary = dispatch_due_unlock_notifications(limit=limit)
    return {
        "processed_count": summary.processed_count,
        "sent_count": summary.sent_count,
        "error_count": summary.error_count,
        "limit": limit,
    }
