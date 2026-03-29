from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from modules.v2.system.ops import run_smoke_checks


class Command(BaseCommand):
    help = "Run production-oriented smoke checks for Django, health endpoint, site configuration, module state, and celery worker ping."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default=getattr(settings, "SITE_BASE_URL", "http://127.0.0.1:8000"),
            help="Public or internal base URL to validate /health/ against.",
        )

    def handle(self, *args, **options):
        results = run_smoke_checks(base_url=options["base_url"])
        has_failures = False
        for result in results:
            style = self.style.SUCCESS if result.ok else self.style.ERROR
            if not result.ok:
                has_failures = True
            self.stdout.write(style(f"[{ 'OK' if result.ok else 'FAIL' }] {result.name}: {result.details}"))

        if has_failures:
            raise CommandError("Smoke check failed.")
