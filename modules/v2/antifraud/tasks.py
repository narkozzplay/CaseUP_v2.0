from celery import shared_task

from .services import detect, prune


@shared_task(name="antifraud.detect_multi_accounts")
def detect_multi_accounts_task() -> dict:
    result = detect()
    return {"groups": result.groups, "new_cases": result.new_cases, "reopened": result.reopened, "shared_networks": result.shared_networks}


@shared_task(name="antifraud.prune_user_ips")
def prune_user_ips_task() -> int:
    return prune()
