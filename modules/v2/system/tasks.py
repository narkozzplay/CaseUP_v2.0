from celery import shared_task

from .backups import run_daily_backup


@shared_task(name="system.daily_backup")
def daily_backup_task() -> dict:
    return run_daily_backup().as_dict()
