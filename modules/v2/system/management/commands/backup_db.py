from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from modules.v2.system.ops import (
    backup_media_directory,
    backup_postgres_database,
    create_backup_paths,
)


class Command(BaseCommand):
    help = "Create PostgreSQL and/or media backups inside BACKUP_ROOT."

    def add_arguments(self, parser):
        parser.add_argument("--name", help="Explicit backup directory name under BACKUP_ROOT.")
        parser.add_argument("--db-only", action="store_true", help="Backup only the PostgreSQL database.")
        parser.add_argument("--media-only", action="store_true", help="Backup only the media directory.")

    def handle(self, *args, **options):
        if options["db_only"] and options["media_only"]:
            raise CommandError("--db-only and --media-only are mutually exclusive.")

        include_db = not options["media_only"]
        include_media = not options["db_only"]
        artifacts = create_backup_paths(
            output_dir_name=options.get("name"),
            include_db=include_db,
            include_media=include_media,
        )

        if artifacts.db_dump_path is not None:
            backup_postgres_database(destination=artifacts.db_dump_path)
            self.stdout.write(self.style.SUCCESS(f"Database backup created: {artifacts.db_dump_path}"))

        if artifacts.media_archive_path is not None:
            backup_media_directory(destination=artifacts.media_archive_path)
            self.stdout.write(self.style.SUCCESS(f"Media backup created: {artifacts.media_archive_path}"))

        self.stdout.write(self.style.SUCCESS(f"Backup directory ready: {artifacts.backup_dir}"))
