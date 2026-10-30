from django.db import migrations

NEW_CASES = {
    "cold-steel",
    "shine-blades",
    "night-blades",
    "glove-box",
    "red-line",
    "pink-rush",
    "purple-haze",
    "blue-print",
    "sniper-nest",
    "rifle-legends",
    "pistol-round",
    "dragon-lair",
    "gradient",
    "all-or-nothing",
    "jackpot",
}


def seed(apps, schema_editor):
    from modules.v2.case_simulator.services import load_case_rows, seed_cases

    # Only the new slugs, so cases removed in Control Center don't come back.
    # Positions follow the full file, so the new cases sort after the existing ones.
    rows = [{**row, "sort_order": (order + 1) * 10} for order, row in enumerate(load_case_rows()) if row["slug"] in NEW_CASES]
    seed_cases(
        rows,
        case_model=apps.get_model("case_simulator", "SimCase"),
        item_model=apps.get_model("case_simulator", "SimCaseItem"),
    )


class Migration(migrations.Migration):
    dependencies = [("case_simulator", "0003_simopening_sold")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
