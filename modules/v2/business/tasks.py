from celery import shared_task

from .report import send_daily_report
from .revenue import sync


@shared_task(name="business.sync_revenue")
def sync_revenue_task() -> dict:
    return sync()


@shared_task(name="business.daily_report")
def daily_report_task() -> bool:
    return send_daily_report()
