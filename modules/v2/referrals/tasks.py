from celery import shared_task

from .services import reconcile


@shared_task(name="referrals.reconcile")
def reconcile_task() -> dict[str, int]:
    return reconcile()
