from celery import shared_task

from .services import verify_listings


@shared_task(name="market.verify_listings")
def verify_listings_task() -> dict:
    return verify_listings()


@shared_task(name="market.process_deals")
def process_deals_task() -> dict:
    from .deals import process_deals

    return process_deals()
