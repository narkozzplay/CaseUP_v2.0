from __future__ import annotations

from django.core.management.base import BaseCommand

from modules.v2.profiles.models import UserRole
from modules.v2.system.ops import grant_role


class Command(BaseCommand):
    help = (
        "Grant an application role and synchronized Django staff/superuser flags to an existing user. "
        "Use the internal Django username (for Steam accounts it is usually steam_<steam_id>)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help="Internal Django username. For Steam-auth users this is usually steam_<steam_id>.",
        )
        parser.add_argument("role", choices=[choice for choice, _label in UserRole.choices])

    def handle(self, *args, **options):
        result = grant_role(username=options["username"], role=options["role"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Role '{result.role}' applied to '{result.username}'. "
                f"is_staff={result.is_staff}, is_superuser={result.is_superuser}, "
                f"profile_created={result.created_profile}."
            )
        )
