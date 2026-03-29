import os

from celery import Celery
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
}