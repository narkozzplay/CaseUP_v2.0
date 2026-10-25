from celery import shared_task

from .sponsors import find_monthly_candidates


@shared_task(name="donations.monthly_sponsors")
def monthly_sponsors_task() -> int:
    return len(find_monthly_candidates())
