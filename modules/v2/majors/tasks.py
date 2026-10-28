from celery import shared_task

from .items import check_major_items


@shared_task(name="majors.check_items")
def check_major_items_task() -> dict:
    return check_major_items()
