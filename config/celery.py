import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("caseup")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Heartbeat + admin alerts (Telegram team chat, push) when a health check breaks or new errors appear.
    "system-watchdog": {
        "task": "system.watchdog",
        "schedule": 120.0,
    },
    "run-due-price-auto-updates": {
        "task": "celery_tasks.run_due_price_auto_updates",
        "schedule": float(settings.CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS),
    },
    # Lowest prices of the whole market, 100 items per request, a few pages per run (price_sweep.py).
    "sweep-market-prices": {
        "task": "celery_tasks.sweep_market_prices",
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
    # Evening nudge (18:05 Moscow time) to users whose daily-bonus streak breaks at midnight.
    "daily-bonus-reminders": {
        "task": "daily_bonus.remind_streaks",
        "schedule": crontab(hour=18, minute=5),
    },
    # Achievement rewards (profile titles and effects) for everyone seen in the last 30 days; the catalog page also checks on open.
    "profile-customization-achievements": {
        "task": "profile_customization.sync_achievements",
        "schedule": crontab(minute=40),
    },
    # Around a Major: new sticker capsules and souvenir packages into the catalog, admins told what is new or missing.
    "majors-check-items": {
        "task": "majors.check_items",
        "schedule": crontab(hour=9, minute=30),
    },
    # 1st of the month: last month's top donors become «Спонсор» candidates; admins get a notification.
    "donations-monthly-sponsors": {
        "task": "donations.monthly_sponsors",
        "schedule": crontab(day_of_month=1, hour=10, minute=0),
    },
    # Market deals: send-deadline reminders, automatic proof of the trade, completion after the hold.
    "market-process-deals": {
        "task": "market.process_deals",
        "schedule": 15 * 60.0,
    },
    # Takes market lots off the showcase when the item left the seller's Steam inventory.
    "market-verify-listings": {
        "task": "market.verify_listings",
        "schedule": 30 * 60.0,
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
    # Closes expired subscriptions and warns users 3 days before the end.
    "subscriptions-lifecycle": {
        "task": "subscriptions.process_lifecycle",
        "schedule": 60 * 60.0,
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
    # Price digest for the public Telegram channel; the posting times are set on the TG bot settings page.
    "tg-channel-price-digest": {
        "task": "tg_notifier.post_channel_digest",
        "schedule": 300.0,
    },
    # Personal bot notices: what users' quiet hours held back, and personal daily digests at their chosen time.
    "tg-personal-tick": {
        "task": "tg_notifier.personal_tick",
        "schedule": 300.0,
    },
    # New support tickets nobody took within 3 hours: one reminder to the team in Telegram.
    "support-escalate-stale-tickets": {
        "task": "support.escalate_stale_tickets",
        "schedule": 600.0,
    },
    "auto-sync-cs2-faceit-stats": {
        "task": "celery_tasks.auto_sync_cs2_faceit_stats",
        "schedule": float(settings.CELERY_BEAT_CS2_FACEIT_STATS_SYNC_SECONDS),
    },
}