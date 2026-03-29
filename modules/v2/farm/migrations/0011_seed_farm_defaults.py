from decimal import Decimal

from django.db import migrations


REWARD_TIERS = [
    {
        "code": "farm_tier_bronze",
        "title": "Bronze Farmer",
        "threshold_turnover_rub": Decimal("1000.00"),
        "reward_title": "Bronze milestone",
        "reward_description": "Первый milestone farmer-пула по продажам через CaseUP.",
        "bonus_value_rub": Decimal("100.00"),
        "sort_order": 10,
    },
    {
        "code": "farm_tier_silver",
        "title": "Silver Farmer",
        "threshold_turnover_rub": Decimal("5000.00"),
        "reward_title": "Silver milestone",
        "reward_description": "Средний reward tier за накопленный оборот CaseUP.",
        "bonus_value_rub": Decimal("350.00"),
        "sort_order": 20,
    },
    {
        "code": "farm_tier_gold",
        "title": "Gold Farmer",
        "threshold_turnover_rub": Decimal("10000.00"),
        "reward_title": "Gold milestone",
        "reward_description": "Старший farmer tier с максимальным бонусом этого этапа.",
        "bonus_value_rub": Decimal("800.00"),
        "sort_order": 30,
    },
]


def seed_farm_defaults(apps, schema_editor):
    FarmModuleConfig = apps.get_model("farm", "FarmModuleConfig")
    FarmerRewardTier = apps.get_model("farm", "FarmerRewardTier")

    FarmModuleConfig.objects.get_or_create(singleton_key=1)

    for payload in REWARD_TIERS:
        FarmerRewardTier.objects.update_or_create(
            code=payload["code"],
            defaults={
                "title": payload["title"],
                "threshold_turnover_rub": payload["threshold_turnover_rub"],
                "reward_title": payload["reward_title"],
                "reward_description": payload["reward_description"],
                "bonus_value_rub": payload["bonus_value_rub"],
                "sort_order": payload["sort_order"],
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("farm", "0010_farmmoduleconfig_farmdropitem_farmdropsession_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_farm_defaults, migrations.RunPython.noop),
    ]
