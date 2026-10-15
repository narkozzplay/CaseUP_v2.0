from celery import shared_task

from .services.bulk_import import run_full_import


@shared_task(name="steam_catalog.import_reference_to_catalog")
def import_reference_to_catalog_task() -> dict:
    return run_full_import()
