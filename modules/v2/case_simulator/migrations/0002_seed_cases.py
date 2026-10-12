from django.db import migrations


def seed(apps, schema_editor):
    from modules.v2.case_simulator.services import load_case_rows, seed_cases

    seed_cases(
        load_case_rows(),
        case_model=apps.get_model("case_simulator", "SimCase"),
        item_model=apps.get_model("case_simulator", "SimCaseItem"),
    )


class Migration(migrations.Migration):
    dependencies = [("case_simulator", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
