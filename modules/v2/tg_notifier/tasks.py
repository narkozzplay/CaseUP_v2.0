from celery import shared_task

from .channel_digest import post_due_digest


@shared_task(name="tg_notifier.post_channel_digest")
def post_channel_digest_task() -> str:
    return post_due_digest()
