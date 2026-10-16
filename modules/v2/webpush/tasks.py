from celery import shared_task

from . import services


@shared_task(name="webpush.send_push", ignore_result=True)
def send_push(user_id: int, push: dict) -> int:
    return services.deliver(user_id, **push)


@shared_task(name="webpush.send_notification_push", ignore_result=True)
def send_notification_push(notification_id: int) -> int:
    from modules.v2.notifications.models import Notification
    from modules.v2.notifications.services import render_notification

    from .categories import category_for

    notification = Notification.objects.filter(pk=notification_id).first()
    if notification is None or notification.is_read:
        return 0
    view = render_notification(notification, "ru")
    return services.deliver(
        notification.recipient_id,
        title=view.title,
        body=view.body,
        url=view.url,
        tag=f"{notification.kind}:{notification.object_id or notification.pk}",
        category=category_for(notification.kind),
    )
