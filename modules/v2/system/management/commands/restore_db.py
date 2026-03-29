from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from modules.v2.system.ops import (
    resolve_restore_artifacts,
    restore_media_directory,
    restore_postgres_database,
)


class Command(BaseCommand):
    help = "Restore PostgreSQL and/or media backups. Requires explicit confirmation flag."

    def add_arguments(self, parser):
        parser.add_argument("--backup-dir", help="Directory name under BACKUP_ROOT containing database.sqlc and/or media.tar.gz.")
        parser.add_argument("--db-dump", help="Explicit path to database dump file.")
        parser.add_argument("--media-archive", help="Explicit path to media archive file.")
        parser.add_argument("--db-only", action="store_true", help="Restore only PostgreSQL database.")
        parser.add_argument("--media-only", action="store_true", help="Restore only media directory.")
        parser.add_argument(
            "--yes-i-understand-this-will-overwrite-data",
            action="store_true",
            help="Required safety flag for destructive restore operation.",
        )

    def handle(self, *args, **options):
        if not options["yes_i_understand_this_will_overwrite_data"]:
            raise CommandError(
                "Restore aborted. Re-run with --yes-i-understand-this-will-overwrite-data to confirm destructive restore."
            )

        if options["db_only"] and options["media_only"]:
            raise CommandError("--db-only and --media-only are mutually exclusive.")
        if not options.get("backup_dir") and not options.get("db_dump") and not options.get("media_archive"):
            raise CommandError("Provide --backup-dir or explicit --db-dump/--media-archive paths.")

        artifacts = resolve_restore_artifacts(
            backup_dir=options.get("backup_dir"),
            db_dump=options.get("db_dump"),
            media_archive=options.get("media_archive"),
        )

        if not options["media_only"]:
            if artifacts.db_dump_path is None:
                raise CommandError("Database restore requested but no database dump was resolved.")
            restore_postgres_database(source=artifacts.db_dump_path)
            self.stdout.write(self.style.SUCCESS(f"Database restore completed: {artifacts.db_dump_path}"))

        if not options["db_only"]:
            if artifacts.media_archive_path is None:
                raise CommandError("Media restore requested but no media archive was resolved.")
            restore_media_directory(source=artifacts.media_archive_path)
            self.stdout.write(self.style.SUCCESS(f"Media restore completed: {artifacts.media_archive_path}"))
