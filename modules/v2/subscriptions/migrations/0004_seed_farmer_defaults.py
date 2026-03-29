from decimal import Decimal

from django.db import migrations


FARMER_FEATURES = [
    {
        "code": "farm_analytics_deep",
        "name": "Farm · Deep Analytics",
        "description": "Полная аналитика farmer drop sessions, item/session/dashboard PnL.",
        "module_key": "farm",
        "sort_order": 110,
    },
    {
        "code": "farm_tg_alerts",
        "name": "Farm · TG Alerts",
        "description": "Пользовательские Telegram-уведомления по farmer drop и milestone-событиям.",
        "module_key": "tg_notifier",
        "sort_order": 120,
    },
    {
        "code": "farm_caseup_reduced_commission",
        "name": "Farm · Reduced CaseUP Commission",
        "description": "Сниженная комиссия при продаже farmer drop через CaseUP marketplace.",
        "module_key": "tradehub",
        "sort_order": 130,
    },
    {
        "code": "farm_sales_advanced_analytics",
        "name": "Farm · Sales Advanced Analytics",
        "description": "Углублённая аналитика продаж по площадкам и эффективности farmer drop.",
        "module_key": "farm",
        "sort_order": 140,
    },
    {
        "code": "farm_bonus_program",
        "name": "Farm · Bonus Program",
        "description": "Доступ к накопительным farmer rewards и milestone-бонусам по обороту CaseUP.",
        "module_key": "farm",
        "sort_order": 150,
    },
    {
        "code": "farm_enhanced_benefits",
        "name": "Farm · Enhanced Benefits",
        "description": "Дополнительные farmer плюшки и резерв под расширенные benefits next stage.",
        "module_key": "farm",
        "sort_order": 160,
    },
]

FARMER_PLANS = [
    {
        "code": "farmer_start",
        "name": "Farmer Start",
        "level": 11,
        "family": "farmer",
        "audience": "farmer",
        "description": "Базовый farmer access: deep analytics + базовый pricing visibility.",
        "ui_badge": "Farmer Start",
        "sort_order": 10,
        "price_rub": Decimal("349.00"),
        "price_floor_rub": Decimal("299.00"),
        "manual_price_adjustment_rub": Decimal("0.00"),
        "billing_period_days": 30,
        "ui_features": [
            "Deep analytics по item / session / dashboard",
            "Текущая цена, delta ₽ и delta %",
            "Базовый farmer workflow без TG-уведомлений",
        ],
        "feature_codes": ["farm_analytics_deep"],
    },
    {
        "code": "farmer_pro",
        "name": "Farmer Pro",
        "level": 12,
        "family": "farmer",
        "audience": "farmer",
        "description": "Farmer analytics + TG alerts + reduced commission + sales breakdown.",
        "ui_badge": "Pro",
        "sort_order": 20,
        "price_rub": Decimal("649.00"),
        "price_floor_rub": Decimal("499.00"),
        "manual_price_adjustment_rub": Decimal("0.00"),
        "billing_period_days": 30,
        "ui_features": [
            "Все возможности Farmer Start",
            "TG alerts по росту / падению / продажам",
            "Сниженная комиссия CaseUP и sales analytics",
        ],
        "feature_codes": [
            "farm_analytics_deep",
            "farm_tg_alerts",
            "farm_caseup_reduced_commission",
            "farm_sales_advanced_analytics",
        ],
    },
    {
        "code": "farmer_ultra",
        "name": "Farmer Ultra",
        "level": 13,
        "family": "farmer",
        "audience": "farmer",
        "description": "Максимальная farmer-линейка: reduced commission, bonus program и enhanced benefits.",
        "ui_badge": "Ultra",
        "sort_order": 30,
        "price_rub": Decimal("899.00"),
        "price_floor_rub": Decimal("699.00"),
        "manual_price_adjustment_rub": Decimal("0.00"),
        "billing_period_days": 30,
        "ui_features": [
            "Все возможности Farmer Pro",
            "Bonus program и milestone rewards",
            "Enhanced benefits для farmer-пула",
        ],
        "feature_codes": [
            "farm_analytics_deep",
            "farm_tg_alerts",
            "farm_caseup_reduced_commission",
            "farm_sales_advanced_analytics",
            "farm_bonus_program",
            "farm_enhanced_benefits",
        ],
    },
]


def seed_farmer_defaults(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionPricingConfig = apps.get_model("subscriptions", "SubscriptionPricingConfig")

    SubscriptionPlan.objects.filter(level__lte=5).update(family="site", audience="general")

    feature_by_code = {}
    for payload in FARMER_FEATURES:
        feature, _ = SubscriptionFeature.objects.update_or_create(
            code=payload["code"],
            defaults={
                "name": payload["name"],
                "description": payload["description"],
                "module_key": payload["module_key"],
                "is_active": True,
                "sort_order": payload["sort_order"],
            },
        )
        feature_by_code[payload["code"]] = feature

    for payload in FARMER_PLANS:
        plan, _ = SubscriptionPlan.objects.update_or_create(
            code=payload["code"],
            defaults={
                "name": payload["name"],
                "level": payload["level"],
                "family": payload["family"],
                "audience": payload["audience"],
                "description": payload["description"],
                "ui_badge": payload["ui_badge"],
                "ui_features": payload["ui_features"],
                "price_rub": payload["price_rub"],
                "price_floor_rub": payload["price_floor_rub"],
                "manual_price_adjustment_rub": payload["manual_price_adjustment_rub"],
                "billing_period_days": payload["billing_period_days"],
                "is_active": True,
                "sort_order": payload["sort_order"],
            },
        )
        plan.features.set([feature_by_code[code].id for code in payload["feature_codes"]])

    SubscriptionPricingConfig.objects.get_or_create(singleton_key=1)


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0003_subscriptionpricingconfig_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_farmer_defaults, migrations.RunPython.noop),
    ]
