from django.core.management.base import BaseCommand, CommandError

from modules.v2.accounts.geoip import database_path, update_database


class Command(BaseCommand):
    help = "Download the DB-IP City Lite database used to show the city of a sign-in."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Download even if the file is recent.")

    def handle(self, *args, **options):
        try:
            status = update_database(force=options["force"])
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{database_path()}: {status}"))
