from django.core.management.base import BaseCommand

from modules.v2.business.revenue import sync


class Command(BaseCommand):
    help = "Add missing rows to the revenue journal from the ledger and completed tradehub orders."

    def handle(self, *args, **options):
        result = sync()
        self.stdout.write(self.style.SUCCESS(f"Revenue journal synced: {result['ledger']} ledger rows, {result['tradehub']} tradehub rows added."))
