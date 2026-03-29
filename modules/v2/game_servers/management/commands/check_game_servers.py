from django.core.management.base import BaseCommand

from modules.v2.game_servers.services import refresh_enabled_game_servers


class Command(BaseCommand):
    help = "Refresh status for all enabled game servers."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Ignore cache TTL and probe all enabled servers.")

    def handle(self, *args, **options):
        servers = refresh_enabled_game_servers(force=options["force"])
        if not servers:
            self.stdout.write(self.style.WARNING("No enabled game servers found."))
            return

        for server in servers:
            latency = f"{server.last_latency_ms}ms" if server.last_latency_ms is not None else "—"
            error = server.last_error or "—"
            self.stdout.write(f"{server.slug}: {server.status} | {server.endpoint} | latency={latency} | error={error}")
