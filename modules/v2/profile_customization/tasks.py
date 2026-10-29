from celery import shared_task

from .services import sync_recent_achievements


@shared_task(name="profile_customization.sync_achievements")
def sync_achievements_task() -> int:
    return sync_recent_achievements()
