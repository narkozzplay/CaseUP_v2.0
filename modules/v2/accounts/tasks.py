from celery import shared_task

from .geoip import update_database
from .sessions import prune_sessions


@shared_task(name="accounts.prune_sessions")
def prune_sessions_task() -> dict[str, int]:
    return prune_sessions()


@shared_task(name="accounts.update_geoip")
def update_geoip_task() -> str:
    return update_database()
