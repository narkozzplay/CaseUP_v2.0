from __future__ import annotations

from decimal import Decimal

from django.db import migrations, models
from django.db.models import Q


CONSTRAINT_NAME = "profiles_userprofile_balance_non_negative"


def validate_existing_balances(apps, schema_editor):
    UserProfile = apps.get_model("profiles", "UserProfile")
    negative_count = UserProfile.objects.filter(balance_coins__lt=Decimal("0.00")).count()
    if negative_count:
        raise RuntimeError(
            "Cannot add non-negative balance constraint: "
            f"found {negative_count} profile(s) with negative balance_coins."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0006_userprofile_last_seen_at"),
    ]

    operations = [
        migrations.RunPython(validate_existing_balances, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="userprofile",
            constraint=models.CheckConstraint(
                condition=Q(balance_coins__gte=Decimal("0.00")),
                name=CONSTRAINT_NAME,
            ),
        ),
    ]
