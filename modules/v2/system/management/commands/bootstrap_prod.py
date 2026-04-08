from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from modules.v2.profiles.models import UserRole
from modules.v2.system.ops import (
    bootstrap_system_state,
    create_or_update_superuser_from_env,
    grant_role,
    validate_critical_prod_env,
)


class Command(BaseCommand):
    help = "Bootstrap production system state, validate env, initialize singleton/module state, and optionally grant admin role."

    def add_arguments(self, parser):
        parser.add_argument("--skip-env-check", action="store_true", help="Skip critical production env validation.")
        parser.add_argument("--no-sync-defaults", action="store_true", help="Do not fill blank SiteConfiguration defaults.")
        parser.add_argument("--username", help="Existing internal Django username to grant a role to during bootstrap. For Steam accounts use steam_<steam_id>.")
        parser.add_argument(
            "--role",
            choices=[choice for choice, _label in UserRole.choices],
            help="Role to grant to --username.",
        )
        parser.add_argument(
            "--create-superuser-from-env",
            action="store_true",
            help="Create or update superuser from DJANGO_SUPERUSER_* environment variables.",
        )

    def handle(self, *args, **options):
        if not options["skip_env_check"]:
            validate_critical_prod_env()
            self.stdout.write(self.style.SUCCESS("Critical production env validation passed."))

        result = bootstrap_system_state(sync_defaults=not options["no_sync_defaults"])
        self.stdout.write(
            self.style.SUCCESS(
                "Bootstrap completed: "
                f"site_configuration.created={result.created_site_configuration}, "
                f"site_configuration.updated={result.updated_site_configuration}, "
                f"module_states.created={list(result.created_module_state_keys)}, "
                f"module_states.updated={list(result.updated_module_state_keys)}"
            )
        )

        username = options.get("username")
        role = options.get("role")
        if username and not role:
            raise CommandError("--role is required when --username is provided.")
        if role and not username:
            raise CommandError("--username is required when --role is provided.")

        if username and role:
            grant_result = grant_role(username=username, role=role)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Granted role '{grant_result.role}' to '{grant_result.username}' "
                    f"(is_staff={grant_result.is_staff}, is_superuser={grant_result.is_superuser})."
                )
            )

        if options["create_superuser_from_env"]:
            super_result = create_or_update_superuser_from_env()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superuser bootstrap completed for '{super_result.username}' "
                    f"(is_staff={super_result.is_staff}, is_superuser={super_result.is_superuser})."
                )
            )
