from django.db import migrations


def backfill(apps, schema_editor):
    FriendRequest = apps.get_model("social_friends", "FriendRequest")
    Notification = apps.get_model("notifications", "Notification")

    already = set(
        Notification.objects.filter(kind="friend_request").values_list("object_id", flat=True)
    )
    for req in FriendRequest.objects.filter(status="pending").iterator():
        if req.pk in already:
            continue
        notification = Notification.objects.create(
            recipient_id=req.to_user_id,
            actor_id=req.from_user_id,
            kind="friend_request",
            object_id=req.pk,
            payload={},
        )
        # Keep the original request time so old requests don't look brand new.
        Notification.objects.filter(pk=notification.pk).update(created_at=req.created_at)


class Migration(migrations.Migration):
    dependencies = [
        ("social_friends", "0001_initial"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
