from django.db import migrations, models


EXTERIOR_ORDER = {
    "Factory New": 1,
    "Minimal Wear": 2,
    "Field-Tested": 3,
    "Well-Worn": 4,
    "Battle-Scarred": 5,
}


def fill_family_fields(apps, schema_editor):
    SteamItem = apps.get_model("steam_catalog", "SteamItem")

    for item in SteamItem.objects.all():
        market_hash_name = (item.market_hash_name or "").strip()

        is_stattrak = market_hash_name.startswith("StatTrak™ ") or market_hash_name.startswith("StatTrak ")
        normalized = market_hash_name
        if normalized.startswith("StatTrak™ "):
            normalized = normalized[len("StatTrak™ "):]
        elif normalized.startswith("StatTrak "):
            normalized = normalized[len("StatTrak "):]

        family_name = market_hash_name
        exterior = ""

        if item.item_type == "skin":
            if normalized.endswith(")") and " (" in normalized:
                family_name = normalized.rsplit(" (", 1)[0].strip()
                exterior = normalized.rsplit("(", 1)[1].rstrip(")").strip()
            else:
                family_name = normalized.strip()

        else:
            family_name = market_hash_name

        item.family_name = family_name
        item.is_stattrak = is_stattrak
        item.wear_order = EXTERIOR_ORDER.get(item.exterior or exterior, 0)
        item.save(update_fields=["family_name", "is_stattrak", "wear_order"])


class Migration(migrations.Migration):

    dependencies = [
        ("steam_catalog", "0002_alter_steamitem_item_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="steamitem",
            name="family_name",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="steamitem",
            name="is_stattrak",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="steamitem",
            name="wear_order",
            field=models.PositiveSmallIntegerField(db_index=True, default=0),
        ),
        migrations.RunPython(fill_family_fields, migrations.RunPython.noop),
    ]