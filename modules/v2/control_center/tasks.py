from celery import shared_task

from .services.activity import prune_activity_log
from .services.users import lift_expired_bans


@shared_task(name="control_center.lift_expired_bans")
def lift_expired_bans_task() -> int:
    return lift_expired_bans()


@shared_task(name="control_center.prune_activity_log")
def prune_activity_log_task() -> dict[str, int]:
    return prune_activity_log()
