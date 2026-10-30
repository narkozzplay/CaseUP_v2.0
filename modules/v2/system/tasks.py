from celery import shared_task

from .backups import run_daily_backup


@shared_task(name="system.daily_backup")
def daily_backup_task() -> dict:
    return run_daily_backup().as_dict()


@shared_task(name="system.watchdog", ignore_result=True)
def watchdog_task() -> dict:
    from .watchdog import run_watchdog

    return run_watchdog()


@shared_task(name="system.server_snapshot", ignore_result=True)
def server_snapshot_task() -> dict:
    from .server_stats import check_overload, take_snapshot

    snapshot = take_snapshot()
    return {"snapshot": snapshot.pk, "alerts": check_overload()}


@shared_task(name="system.hosting_check", ignore_result=True)
def hosting_check_task() -> dict:
    from .hosting import run_hosting_check

    return run_hosting_check()
