from __future__ import annotations

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def _populate_family_and_cleanup(apps, schema_editor):
    UserSubscription = apps.get_model("subscriptions", "UserSubscription")
    now = timezone.now()

    for subscription in UserSubscription.objects.select_related("plan").all().order_by("id").iterator():
        target_family = getattr(subscription.plan, "family", "site") or "site"
        changed_fields: list[str] = []

        if subscription.family != target_family:
            subscription.family = target_family
            changed_fields.append("family")

        if (
            subscription.status == "active"
            and subscription.expires_at is not None
            and subscription.expires_at <= now
        ):
            subscription.status = "expired"
            changed_fields.append("status")

        if changed_fields:
            changed_fields.append("updated_at")
            subscription.save(update_fields=changed_fields)

    seen_keys: set[tuple[int, str]] = set()
    duplicate_active_ids: list[int] = []
    active_qs = (
        UserSubscription.objects
        .filter(status="active")
        .order_by("user_id", "family", "-starts_at", "-id")
    )
    for subscription in active_qs.iterator():
        key = (subscription.user_id, subscription.family)
        if key in seen_keys:
            duplicate_active_ids.append(subscription.id)
            continue
        seen_keys.add(key)

    if duplicate_active_ids:
        UserSubscription.objects.filter(id__in=duplicate_active_ids).update(
            status="canceled",
            updated_at=now,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0006_seed_site_plan_prices"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersubscription",
            name="family",
            field=models.CharField(
                choices=[("site", "Site"), ("farmer", "Farmer")],
                db_index=True,
                default="site",
                max_length=16,
            ),
        ),
        migrations.RunPython(_populate_family_and_cleanup, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["user", "family", "status"], name="sub_usr_fam_stat_idx"),
        ),
        migrations.AddConstraint(
            model_name="usersubscription",
            constraint=models.UniqueConstraint(
                condition=Q(status="active"),
                fields=("user", "family"),
                name="sub_usr_family_active_uniq",
            ),
        ),
    ]
