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


@shared_task(name="tg_notifier.mirror_notification", ignore_result=True)
def mirror_notification_task(notification_id: int) -> str:
    from .delivery import mirror_notification
    from .services import TelegramNotifierError

    try:
        return mirror_notification(notification_id)
    except TelegramNotifierError as exc:
        return f"error: {exc}"


@shared_task(name="tg_notifier.personal_tick", ignore_result=True)
def personal_tick_task() -> str:
    """Every few minutes: what quiet hours held back, and personal digests whose time has come."""
    from .delivery import flush_deferred, send_due_digests
    from .portfolio_weekly import send_due_weekly

    return f"deferred={flush_deferred()} digests={send_due_digests()} weekly={send_due_weekly()}"
