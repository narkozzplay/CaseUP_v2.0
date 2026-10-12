from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from modules.v2.steam_catalog.models import CS2ReferenceItem
from modules.v2.steam_catalog.services.reference import (
    DEFAULT_KINDS,
    REFERENCE_FILES,
    fetch_reference_payload,
    import_reference_rows,
)


class Command(BaseCommand):
    help = "Импорт справочника предметов CS2 из открытого датасета ByMykel/CSGO-API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kinds",
            default=",".join(DEFAULT_KINDS),
            help="Через запятую: skin, case, sticker. По умолчанию skin,case.",
        )
        parser.add_argument(
            "--from-dir",
            default="",
            help="Папка с локальными skins.json / crates.json / stickers.json вместо загрузки из сети.",
        )
        parser.add_argument("--lang", default="en", help="Язык датасета (en по умолчанию).")

    def handle(self, *args, **options):
        kinds = [kind.strip() for kind in options["kinds"].split(",") if kind.strip()]
        unknown = [kind for kind in kinds if kind not in CS2ReferenceItem.Kind.values]
        if unknown:
            raise CommandError(f"Неизвестные типы: {', '.join(unknown)}")

        source_dir = Path(options["from_dir"]) if options["from_dir"] else None
        for kind in kinds:
            if source_dir is not None:
                path = source_dir / f"{REFERENCE_FILES[kind]}.json"
                if not path.exists():
                    raise CommandError(f"Файл не найден: {path}")
                rows = json.loads(path.read_text(encoding="utf-8"))
            else:
                self.stdout.write(f"Загружаю {REFERENCE_FILES[kind]}.json…")
                try:
                    rows = fetch_reference_payload(kind=kind, lang=options["lang"])
                except Exception as exc:
                    raise CommandError(f"Не удалось загрузить {kind}: {exc}") from exc

            result = import_reference_rows(kind=kind, rows=rows)
            self.stdout.write(self.style.SUCCESS(
                f"{kind}: создано {result.created}, обновлено {result.updated}, пропущено {result.skipped}"
            ))
