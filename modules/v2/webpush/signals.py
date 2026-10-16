"""Every new (or re-raised) notification is mirrored to the recipient's devices."""
from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from modules.v2.notifications.models import Notification

from .models import PushSubscription


@receiver(post_save, sender=Notification, dispatch_uid="webpush_mirror_notification")
def mirror_notification(sender, instance: Notification, created: bool, update_fields=None, **kwargs):
    raised_again = update_fields is not None and "created_at" in update_fields
    if instance.is_read or not (created or raised_again):
        return
    if not PushSubscription.objects.filter(user_id=instance.recipient_id).exists():
        return
    from .tasks import send_notification_push

    transaction.on_commit(lambda: send_notification_push.delay(instance.pk))
