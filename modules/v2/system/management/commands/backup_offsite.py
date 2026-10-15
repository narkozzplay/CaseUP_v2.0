from django.core.management.base import BaseCommand, CommandError

from modules.v2.system.backups import check_connection, run_daily_backup, sftp_configured


class Command(BaseCommand):
    help = "Off-site backups: --test checks the SFTP connection, --now runs the daily backup immediately."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--test", action="store_true", help="Connect to the SFTP server and list stored backups.")
        group.add_argument("--now", action="store_true", help="Make a backup now, upload it and remove old copies.")

    def handle(self, *args, **options):
        if options["test"]:
            if not sftp_configured():
                raise CommandError("SFTP is not configured: set BACKUP_SFTP_HOST, BACKUP_SFTP_USER and BACKUP_SFTP_PASSWORD in .env.prod")
            try:
                info = check_connection()
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"SFTP connection failed: {exc}") from exc
            self.stdout.write(self.style.SUCCESS("SFTP connection OK"))
            self.stdout.write(f"Server key: {info['fingerprint']}")
            self.stdout.write(f"Folder: {info['path']} — backups stored: {len(info['backups'])}")
            for name in info["backups"][-5:]:
                self.stdout.write(f"  {name}")
            return
        report = run_daily_backup()
        if not report.ok:
            raise CommandError(f"Backup failed: {report.error}")
        size_mb = report.size_bytes / 1024 / 1024
        where = f"uploaded to {report.remote_path}" if report.uploaded else "kept on this server only (SFTP not configured)"
        self.stdout.write(self.style.SUCCESS(f"Backup {report.name}: {size_mb:.1f} MB, {where}"))
        if report.local_removed or report.remote_removed:
            self.stdout.write(f"Removed old copies: local {len(report.local_removed)}, remote {len(report.remote_removed)}")
