from celery import shared_task

from .lifecycle import process_subscription_lifecycle


@shared_task(name="subscriptions.process_lifecycle")
def process_subscription_lifecycle_task() -> dict[str, int]:
    return process_subscription_lifecycle()
