from pathlib import Path

from django.core.management.base import BaseCommand

from modules.v2.case_simulator.services import load_case_rows, seed_cases


class Command(BaseCommand):
    help = "Создаёт кейсы симулятора (CS2, CaseUP и терминал) из data/cases.json."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Другой JSON с кейсами.")
        parser.add_argument("--update", action="store_true", help="Перезаписать существующие кейсы и их содержимое.")

    def handle(self, *args, **options):
        rows = load_case_rows(Path(options["file"]) if options.get("file") else None)
        cases, items = seed_cases(rows, update_existing=options["update"])
        self.stdout.write(self.style.SUCCESS(f"Кейсов создано: {cases}, предметов записано: {items}."))
