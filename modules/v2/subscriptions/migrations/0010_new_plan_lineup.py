"""New plan lineup (25.10.2026).

Site: free catalog with ads -> "Без рекламы" 99 ₽ -> "Плюс" 199 ₽ -> "Pro" 349 ₽.
Farmer: "Farmer" 349 ₽ and "Farmer Pro" 649 ₽; Farmer Ultra is retired.
Everyone who ever had any subscription gets Pro for 30 days as a thank-you.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import migrations
from django.utils import timezone


GIFT_DAYS = 30
GIFT_NOTE = "Переход на новые тарифы: Pro на 30 дней в подарок"

NEW_FEATURES = [
    {
        "code": "no_ads",
        "name": "Без рекламы",
        "description": "Реклама не показывается ни на одной странице сайта.",
        "sort_order": 5,
    },
    {
        "code": "profile_customization",
        "name": "Кастомизация профиля",
        "description": "Эффекты CaseUP, анимированные фоны и аватарки из Steam в профиле.",
        "module_key": "profile_customization",
        "sort_order": 70,
    },
]

FEATURE_RENAMES = {
    "steam_catalog_view": ("Каталог и графики цен", "Каталог предметов, текущие цены и график цены. Бесплатно после входа."),
    "steam_catalog_deep_analytics": ("Анализ цены", "Тренд, мин/макс, просадка, сравнение периода, сырая история цен."),
    "tg_notifier_user": ("Telegram-уведомления", "Алерты и уведомления в Telegram."),
    "steam_trading_advanced_analytics": ("Аналитика инвестиций", "Расширенная аналитика портфеля «Инвестиции»."),
    "site_store_full_access": ("Магазин тем по подписке", "Все темы магазина, включённые в подписку."),
    "farm_analytics_deep": ("Аналитика фермы", "Глубокая аналитика фермы и дропов."),
    "farm_tg_alerts": ("TG-алерты фермы", "Уведомления о росте, падении и продажах."),
    "farm_caseup_reduced_commission": ("Сниженная комиссия CaseUP", "Пониженная комиссия при продаже через CaseUP."),
    "farm_sales_advanced_analytics": ("Аналитика продаж", "Расширенная аналитика продаж фермы."),
    "farm_bonus_program": ("Бонусная программа", "Бонусы и награды за этапы фермы."),
}

RETIRED_FEATURES = ("premium_future_access", "farm_enhanced_benefits")

SITE_PLANS = [
    {
        "code": "site_no_ads",
        "name": "Без рекламы",
        "level": 21,
        "sort_order": 10,
        "price_rub": Decimal("99.00"),
        "ui_badge": "No Ads",
        "description": "Всё, что есть бесплатно, но без рекламы на всём сайте.",
        "ui_features": [
            "Без рекламы на всём сайте",
            "Каталог, графики, избранное",
            "Значок поддержавшего в профиле",
        ],
        "limits": {},
    },
    {
        "code": "site_plus",
        "name": "Плюс",
        "level": 22,
        "sort_order": 20,
        "price_rub": Decimal("199.00"),
        "ui_badge": "Plus",
        "description": "Анализ цен и уведомления для активного трейдера.",
        "ui_features": [
            "Всё из «Без рекламы»",
            "Анализ цены: тренд, мин/макс, просадка, сырая история",
            "До 25 ценовых алертов",
            "Telegram-уведомления",
        ],
        "limits": {"price_alerts": 25},
    },
    {
        "code": "site_pro",
        "name": "Pro",
        "level": 23,
        "sort_order": 30,
        "price_rub": Decimal("349.00"),
        "ui_badge": "Pro",
        "description": "Максимум возможностей CaseUP.",
        "ui_features": [
            "Всё из «Плюс»",
            "Аналитика портфеля «Инвестиции»",
            "Алерты без лимита",
            "Магазин тем по подписке",
            "Кастомизация профиля",
        ],
        "limits": {"price_alerts": None},
    },
]

FARMER_PLAN_UPDATES = {
    "farmer_start": {
        "name": "Farmer",
        "price_rub": Decimal("349.00"),
        "price_floor_rub": Decimal("299.00"),
        "ui_badge": "Farmer",
        "description": "Аналитика фермы и TG-алерты, без рекламы.",
        "ui_features": [
            "Глубокая аналитика фермы",
            "TG-алерты по росту, падению и продажам",
            "Без рекламы на всём сайте",
        ],
        "sort_order": 10,
    },
    "farmer_pro": {
        "name": "Farmer Pro",
        "price_rub": Decimal("649.00"),
        "price_floor_rub": Decimal("499.00"),
        "ui_badge": "Pro",
        "description": "Для тех, кто продаёт много: сниженная комиссия, аналитика продаж и бонусы.",
        "ui_features": [
            "Всё из Farmer",
            "Сниженная комиссия CaseUP",
            "Аналитика продаж",
            "Бонусная программа",
        ],
        "sort_order": 20,
    },
}

# feature code -> (required plan code, value in the pricing formula, sort order); a plan gets every
# feature whose required plan is at or below its level.
SITE_RULES = {
    "no_ads": ("site_no_ads", Decimal("0.00"), 5),
    "steam_catalog_view": ("site_no_ads", Decimal("0.00"), 10),
    "steam_catalog_deep_analytics": ("site_plus", Decimal("30.00"), 20),
    "tg_notifier_user": ("site_plus", Decimal("20.00"), 30),
    "steam_trading_advanced_analytics": ("site_pro", Decimal("50.00"), 40),
    "site_store_full_access": ("site_pro", Decimal("30.00"), 50),
    "profile_customization": ("site_pro", Decimal("20.00"), 60),
}
FARMER_RULES = {
    "no_ads": ("farmer_start", Decimal("0.00"), 105),
    "farm_analytics_deep": ("farmer_start", Decimal("60.00"), 110),
    "farm_tg_alerts": ("farmer_start", Decimal("40.00"), 120),
    "farm_caseup_reduced_commission": ("farmer_pro", Decimal("0.00"), 130),
    "farm_sales_advanced_analytics": ("farmer_pro", Decimal("100.00"), 140),
    "farm_bonus_program": ("farmer_pro", Decimal("30.00"), 150),
}

OLD_SITE_TO_NEW = {
    "level_1_viewer": "site_plus",
    "level_2_analytics": "site_plus",
    "level_3_notifier": "site_plus",
    "level_4_investor": "site_pro",
    "level_5_pro": "site_pro",
}


def _sync_family(SubscriptionPlan, SubscriptionFeaturePricingRule, family):
    plans = list(SubscriptionPlan.objects.filter(family=family))
    rules = list(
        SubscriptionFeaturePricingRule.objects.filter(family=family, is_enabled=True, required_plan__isnull=False)
        .select_related("required_plan")
    )
    for plan in plans:
        feature_ids = sorted({rule.feature_id for rule in rules if plan.level >= rule.required_plan.level})
        plan.features.set(feature_ids)


def forwards(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionFeaturePricingRule = apps.get_model("subscriptions", "SubscriptionFeaturePricingRule")
    SubscriptionPricingConfig = apps.get_model("subscriptions", "SubscriptionPricingConfig")
    UserSubscription = apps.get_model("subscriptions", "UserSubscription")
    Notification = apps.get_model("notifications", "Notification")

    now = timezone.now()

    # Features
    for payload in NEW_FEATURES:
        SubscriptionFeature.objects.update_or_create(
            code=payload["code"],
            defaults={
                "name": payload["name"],
                "description": payload["description"],
                "module_key": payload.get("module_key", ""),
                "sort_order": payload["sort_order"],
                "is_active": True,
            },
        )
    for code, (name, description) in FEATURE_RENAMES.items():
        SubscriptionFeature.objects.filter(code=code).update(name=name, description=description)
    SubscriptionFeature.objects.filter(code="steam_catalog_view").update(free_tier="registered")
    SubscriptionFeature.objects.filter(code__in=RETIRED_FEATURES).update(is_active=False)

    # Plans
    SubscriptionPlan.objects.filter(code__in=OLD_SITE_TO_NEW.keys()).update(is_active=False)
    SubscriptionPlan.objects.filter(code="farmer_ultra").update(is_active=False)
    for payload in SITE_PLANS:
        SubscriptionPlan.objects.update_or_create(
            code=payload["code"],
            defaults={
                "name": payload["name"],
                "level": payload["level"],
                "family": "site",
                "audience": "general",
                "description": payload["description"],
                "ui_badge": payload["ui_badge"],
                "ui_features": payload["ui_features"],
                "limits": payload["limits"],
                "price_rub": payload["price_rub"],
                "price_floor_rub": Decimal("0.00"),
                "manual_price_adjustment_rub": Decimal("0.00"),
                "billing_period_days": 30,
                "is_active": True,
                "sort_order": payload["sort_order"],
            },
        )
    for code, fields in FARMER_PLAN_UPDATES.items():
        SubscriptionPlan.objects.filter(code=code).update(**fields)

    # Feature rules decide which plan gets which feature.
    plans_by_code = {plan.code: plan for plan in SubscriptionPlan.objects.all()}
    features_by_code = {feature.code: feature for feature in SubscriptionFeature.objects.all()}
    SubscriptionFeaturePricingRule.objects.filter(feature__code__in=RETIRED_FEATURES).update(is_enabled=False)
    for family, rules in (("site", SITE_RULES), ("farmer", FARMER_RULES)):
        for feature_code, (plan_code, value_rub, sort_order) in rules.items():
            plan = plans_by_code.get(plan_code)
            feature = features_by_code.get(feature_code)
            if plan is None or feature is None:
                continue
            SubscriptionFeaturePricingRule.objects.update_or_create(
                feature=feature,
                family=family,
                defaults={"required_plan": plan, "value_rub": value_rub, "is_enabled": True, "sort_order": sort_order},
            )
        _sync_family(SubscriptionPlan, SubscriptionFeaturePricingRule, family)

    # Recommended-price formula tuned to the new prices.
    config, _created = SubscriptionPricingConfig.objects.get_or_create(singleton_key=1)
    config.site_base_price_rub = Decimal("99.00")
    config.site_level_step_rub = Decimal("50.00")
    config.farmer_base_price_rub = Decimal("249.00")
    config.farmer_level_step_rub = Decimal("99.00")
    config.save()

    # Farmer Ultra subscribers keep their term on Farmer Pro.
    farmer_pro = plans_by_code.get("farmer_pro")
    if farmer_pro is not None:
        UserSubscription.objects.filter(plan__code="farmer_ultra", status="active").update(plan=farmer_pro, updated_at=now)

    # Pro for 30 days to everyone who ever had any subscription.
    site_pro = plans_by_code["site_pro"]
    gift_until = now + timedelta(days=GIFT_DAYS)
    user_ids = set(UserSubscription.objects.values_list("user_id", flat=True).distinct())
    for user_id in user_ids:
        active = list(UserSubscription.objects.filter(user_id=user_id, family="site", status="active"))
        expires_at = gift_until
        for subscription in active:
            if subscription.expires_at is None or subscription.expires_at > now:
                # Someone who paid for longer than the gift keeps the longer term.
                if subscription.expires_at is None:
                    expires_at = None
                elif expires_at is not None and subscription.expires_at > expires_at:
                    expires_at = subscription.expires_at
                subscription.status = "canceled"
                subscription.expires_at = now
                subscription.notes = "\n".join(part for part in (subscription.notes, "Заменена на Pro при переходе на новые тарифы") if part)
            else:
                subscription.status = "expired"
            subscription.updated_at = now
            subscription.save(update_fields=["status", "expires_at", "notes", "updated_at"])
        gift = UserSubscription.objects.create(
            user_id=user_id,
            plan=site_pro,
            family="site",
            status="active",
            starts_at=now,
            expires_at=expires_at,
            notes=GIFT_NOTE,
        )
        Notification.objects.create(
            recipient_id=user_id,
            kind="subscription_gift",
            object_id=gift.pk,
            payload={
                "title": "Pro на 30 дней в подарок",
                "body": "Мы обновили тарифы. Спасибо, что были с нами: дарим Pro на 30 дней. Каталог и графики цен теперь бесплатны для всех.",
                "url": "/subscriptions/",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0009_plan_limits_free_tier_periods"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
