from celery import shared_task

from .services import reconcile_pending_payments


@shared_task(name="payments.reconcile_pending_payments")
def reconcile_pending_payments_task() -> dict[str, int]:
    return reconcile_pending_payments()
