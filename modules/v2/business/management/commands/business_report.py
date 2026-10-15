from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from modules.v2.business.report import build_daily_report, send_daily_report


class Command(BaseCommand):
    help = "Print or e-mail the daily business report (yesterday by default)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Day to report, YYYY-MM-DD (default: yesterday).")
        parser.add_argument("--send", action="store_true", help="E-mail the report to ADMIN_EMAILS instead of printing it.")

    def handle(self, *args, **options):
        day = None
        if options["date"]:
            try:
                day = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--date must look like 2026-10-15") from exc
        if options["send"]:
            if not send_daily_report(day=day):
                raise CommandError("Report not sent: ADMIN_EMAILS is empty or mail failed (see logs).")
            self.stdout.write(self.style.SUCCESS("Report sent."))
            return
        subject, text, _html = build_daily_report(day=day)
        self.stdout.write(subject)
        self.stdout.write(text)
