from django.db import migrations


FEATURES = [
    {
        "code": "steam_catalog_view",
        "name": "Steam Catalog · Visual View",
        "description": "Базовый визуальный просмотр каталога Steam без операций редактирования.",
        "module_key": "steam_catalog",
        "sort_order": 10,
    },
    {
        "code": "steam_catalog_deep_analytics",
        "name": "Steam Catalog · Deep Analytics",
        "description": "Графики, история цен и расширенная аналитика карточек каталога.",
        "module_key": "steam_catalog",
        "sort_order": 20,
    },
    {
        "code": "tg_notifier_user",
        "name": "TG Notifier · User Functions",
        "description": "Пользовательский центр уведомлений и ручные TG-отправки из инвестиций.",
        "module_key": "tg_notifier",
        "sort_order": 30,
    },
    {
        "code": "steam_trading_advanced_analytics",
        "name": "Steam Trading · Advanced Analytics",
        "description": "Расширенная аналитика портфеля, KPI, таймлайн разблокировок и ranking блоки.",
        "module_key": "steam_trading",
        "sort_order": 40,
    },
    {
        "code": "premium_future_access",
        "name": "Premium Future Access",
        "description": "Резерв для будущих premium-возможностей уровня Pro.",
        "module_key": "",
        "sort_order": 50,
    },
]

PLANS = [
    {
        "code": "level_1_viewer",
        "name": "Level 1 · Viewer",
        "level": 1,
        "description": "Базовый доступ к визуальному просмотру Steam Catalog без редакторских действий.",
        "ui_badge": "Starter",
        "sort_order": 10,
        "ui_features": [
            "Визуальный просмотр карточек Steam Catalog",
            "Детальная страница предмета без ручных операций",
            "Подготовка к апгрейду на аналитику",
        ],
        "feature_codes": ["steam_catalog_view"],
    },
    {
        "code": "level_2_analytics",
        "name": "Level 2 · Analytics",
        "level": 2,
        "description": "Открывает глубокую аналитику Steam Catalog поверх базового просмотра.",
        "ui_badge": "Analytics",
        "sort_order": 20,
        "ui_features": [
            "Все возможности Level 1",
            "История цен и графики Steam Catalog",
            "Аналитика от старта периода и от пика",
        ],
        "feature_codes": ["steam_catalog_view", "steam_catalog_deep_analytics"],
    },
    {
        "code": "level_3_notifier",
        "name": "Level 3 · Notifier",
        "level": 3,
        "description": "Добавляет пользовательские TG-функции и центр уведомлений.",
        "ui_badge": "Notifier",
        "sort_order": 30,
        "ui_features": [
            "Все возможности Level 2",
            "Центр уведомлений TG Notifier",
            "Ручная TG-отправка из Steam Investments",
        ],
        "feature_codes": ["steam_catalog_view", "steam_catalog_deep_analytics", "tg_notifier_user"],
    },
    {
        "code": "level_4_investor",
        "name": "Level 4 · Investor",
        "level": 4,
        "description": "Расширенная аналитика Steam Trading / Investments для активной работы с портфелем.",
        "ui_badge": "Investor",
        "sort_order": 40,
        "ui_features": [
            "Все возможности Level 3",
            "KPI и PnL по активным позициям",
            "Разблокировки, ranking и paywalled analytics blocks",
        ],
        "feature_codes": ["steam_catalog_view", "steam_catalog_deep_analytics", "tg_notifier_user", "steam_trading_advanced_analytics"],
    },
    {
        "code": "level_5_pro",
        "name": "Level 5 · Pro",
        "level": 5,
        "description": "Полный текущий набор и резерв под будущие premium features.",
        "ui_badge": "Pro",
        "sort_order": 50,
        "ui_features": [
            "Все возможности Level 4",
            "Резерв под будущие premium-модули",
            "Максимальный уровень foundation на этапе 1",
        ],
        "feature_codes": ["steam_catalog_view", "steam_catalog_deep_analytics", "tg_notifier_user", "steam_trading_advanced_analytics", "premium_future_access"],
    },
]


def seed_defaults(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")

    feature_by_code = {}
    for payload in FEATURES:
        feature, _created = SubscriptionFeature.objects.update_or_create(
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

    for payload in PLANS:
        plan, _created = SubscriptionPlan.objects.update_or_create(
            code=payload["code"],
            defaults={
                "name": payload["name"],
                "level": payload["level"],
                "description": payload["description"],
                "ui_badge": payload["ui_badge"],
                "ui_features": payload["ui_features"],
                "is_active": True,
                "sort_order": payload["sort_order"],
            },
        )
        plan.features.set([feature_by_code[code].id for code in payload["feature_codes"]])


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
