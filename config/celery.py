import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("caseup")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "run-due-price-auto-updates": {
        "task": "celery_tasks.run_due_price_auto_updates",
        "schedule": float(settings.CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS),
    },
    "dispatch-pending-price-drop-alerts": {
        "task": "celery_tasks.dispatch_pending_price_drop_alerts",
        "schedule": float(settings.CELERY_BEAT_PENDING_ALERT_DISPATCHER_SECONDS),
    },
    "dispatch-due-steam-trade-unlocks": {
        "task": "celery_tasks.dispatch_due_steam_trade_unlocks",
        "schedule": float(settings.CELERY_BEAT_STEAM_TRADING_UNLOCK_NOTIFIER_SECONDS),
    },
    # CS2 stats auto-update — every 30 minutes (configurable via env)
    "auto-sync-cs2-steam-stats": {
        "task": "celery_tasks.auto_sync_cs2_steam_stats",
        "schedule": float(settings.CELERY_BEAT_CS2_STEAM_STATS_SYNC_SECONDS),
    },
    "prune-user-sessions": {
        "task": "accounts.prune_sessions",
        "schedule": 24 * 60 * 60.0,
    },
    # Checked daily, downloads only when the local file is older than ~25 days.
    "update-geoip-database": {
        "task": "accounts.update_geoip",
        "schedule": 24 * 60 * 60.0,
    },
    "prune-activity-log": {
        "task": "control_center.prune_activity_log",
        "schedule": 24 * 60 * 60.0,
    },
    "lift-expired-user-bans": {
        "task": "control_center.lift_expired_bans",
        "schedule": 300.0,
    },
    # Database + media copy every night (04:15 Moscow time), uploaded to the SFTP backup host when configured.
    "daily-backup": {
        "task": "system.daily_backup",
        "schedule": crontab(hour=4, minute=15),
    },
    "detect-multi-accounts": {
        "task": "antifraud.detect_multi_accounts",
        "schedule": 60 * 60.0,
    },
    "prune-user-ips": {
        "task": "antifraud.prune_user_ips",
        "schedule": 24 * 60 * 60.0,
    },
    # Fills the revenue journal for anything the live hooks missed.
    "sync-revenue": {
        "task": "business.sync_revenue",
        "schedule": 60 * 60.0,
    },
    # Yesterday's numbers to ADMIN_EMAILS every morning (09:05 Moscow time).
    "business-daily-report": {
        "task": "business.daily_report",
        "schedule": crontab(hour=9, minute=5),
    },
    "reconcile-referrals": {
        "task": "referrals.reconcile",
        "schedule": 6 * 60 * 60.0,
    },
    # Picks up top-ups whose webhook was lost or whose payer never came back to the site.
    "reconcile-pending-payments": {
        "task": "payments.reconcile_pending_payments",
        "schedule": float(settings.CELERY_BEAT_PAYMENTS_RECONCILE_SECONDS),
    },
    "auto-sync-cs2-faceit-stats": {
        "task": "celery_tasks.auto_sync_cs2_faceit_stats",
        "schedule": float(settings.CELERY_BEAT_CS2_FACEIT_STATS_SYNC_SECONDS),
    },
}