from celery import shared_task

from .channel_digest import post_due_digest


@shared_task(name="tg_notifier.post_channel_digest")
def post_channel_digest_task() -> str:
    return post_due_digest()


@shared_task(name="tg_notifier.notify_admins_signup", ignore_result=True)
def notify_admins_signup_task(user_id: int) -> int:
    from .admin_events import notify_signup

    return notify_signup(user_id)


@shared_task(name="tg_notifier.notify_admins_topup", ignore_result=True)
def notify_admins_topup_task(entry_id: int) -> int:
    from .admin_events import notify_topup

    return notify_topup(entry_id)
