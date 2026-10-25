from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from modules.v2.farm.models import FarmModuleConfig

from .feature_codes import FARM_CASEUP_REDUCED_COMMISSION
from .models import SubscriptionPlan
from .services import get_feature_pricing_rules, get_subscription_pricing_config


ZERO = Decimal("0.00")
ONE_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class PricingFactor:
    key: str
    label: str
    value_rub: Decimal
    explanation: str


@dataclass(frozen=True)
class PlanPricingEvaluation:
    plan: SubscriptionPlan
    recommended_price_rub: Decimal
    current_price_rub: Decimal
    delta_rub: Decimal
    delta_percent: Decimal
    price_status: str
    price_status_label: str
    factors: tuple[PricingFactor, ...]
    explanation: str


STATUS_UNDERPRICED = "underpriced"
STATUS_REASONABLE = "reasonable"
STATUS_OVERPRICED = "overpriced"


def _q_money(value: Decimal | int | float | str | None) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q_percent(value: Decimal | int | float | str | None) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _feature_code_set(plan: SubscriptionPlan) -> set[str]:
    return {feature.code for feature in plan.features.all() if feature.is_active}


def _get_family_position(plan: SubscriptionPlan) -> int:
    sibling_ids = list(
        SubscriptionPlan.objects.filter(family=plan.family, is_active=True)
        .order_by("sort_order", "level", "id")
        .values_list("id", flat=True)
    )
    try:
        return sibling_ids.index(plan.id) + 1
    except ValueError:
        return 1


def evaluate_plan_pricing(plan: SubscriptionPlan) -> PlanPricingEvaluation:
    pricing_config = get_subscription_pricing_config()
    farm_config, _created = FarmModuleConfig.objects.get_or_create(singleton_key=1)
    feature_codes = _feature_code_set(plan)
    factors: list[PricingFactor] = []

    # The step counts the plan's position in its line, so level numbers can have gaps.
    steps = Decimal(max(_get_family_position(plan) - 1, 0))
    if plan.family == SubscriptionPlan.Family.FARMER:
        base_value = _q_money(pricing_config.farmer_base_price_rub)
        step_value = _q_money(pricing_config.farmer_level_step_rub * steps)
    else:
        base_value = _q_money(pricing_config.site_base_price_rub)
        step_value = _q_money(pricing_config.site_level_step_rub * steps)

    factors.append(PricingFactor("base", "Базовая ценность линейки", base_value, "Стартовая цена для выбранного семейства тарифов."))
    if step_value > ZERO:
        factors.append(PricingFactor("level", "Надбавка за ступень тарифа", step_value, "Более высокий тариф получает дополнительную ценность в формуле."))

    rules = get_feature_pricing_rules(family=plan.family)
    for rule in rules:
        feature = rule.feature
        if not rule.is_enabled or not feature.is_active or feature.code not in feature_codes:
            continue
        value = _q_money(rule.value_rub)
        if value <= ZERO:
            continue
        required_plan_name = rule.required_plan.name if rule.required_plan_id else "без привязки"
        explanation = f"Фича {feature.name} стоит {value:.2f} ₽ и открывается с плана {required_plan_name}."
        factors.append(PricingFactor(feature.code, feature.name, value, explanation))

    if FARM_CASEUP_REDUCED_COMMISSION in feature_codes:
        fee_delta = _q_percent(farm_config.default_caseup_fee_percent - farm_config.subscribed_caseup_fee_percent)
        commission_component = _q_money(
            pricing_config.reduced_commission_reference_turnover_rub
            * (fee_delta / ONE_HUNDRED)
            * (pricing_config.reduced_commission_capture_ratio_percent / ONE_HUNDRED)
        )
        if commission_component > ZERO:
            explanation = (
                f"Модель учитывает снижение комиссии с {farm_config.default_caseup_fee_percent}% "
                f"до {farm_config.subscribed_caseup_fee_percent}% на опорном обороте "
                f"{pricing_config.reduced_commission_reference_turnover_rub} ₽."
            )
            factors.append(PricingFactor("commission", "Сниженная комиссия CaseUP", commission_component, explanation))

    raw_recommendation = sum((factor.value_rub for factor in factors), ZERO)
    manual_adjustment = _q_money(plan.manual_price_adjustment_rub)
    if manual_adjustment:
        label = "Ручная корректировка"
        explanation = "Администратор вручную скорректировал рекомендованную цену для этого плана."
        factors.append(PricingFactor("manual_adjustment", label, manual_adjustment, explanation))
        raw_recommendation = _q_money(raw_recommendation + manual_adjustment)

    floor_value = max(_q_money(pricing_config.global_price_floor_rub), _q_money(plan.price_floor_rub))
    recommended = max(floor_value, _q_money(raw_recommendation))
    current = _q_money(plan.price_rub)
    delta = _q_money(current - recommended)
    delta_percent = _q_percent((delta / recommended) * ONE_HUNDRED) if recommended > ZERO else ZERO

    threshold = _q_percent(pricing_config.reasonable_delta_percent)
    if delta_percent < -threshold:
        status = STATUS_UNDERPRICED
        label = "цена занижена"
    elif delta_percent > threshold:
        status = STATUS_OVERPRICED
        label = "цена завышена"
    else:
        status = STATUS_REASONABLE
        label = "цена в норме"

    explanation = " ".join(f"{factor.label}: {factor.explanation}" for factor in factors)

    return PlanPricingEvaluation(
        plan=plan,
        recommended_price_rub=recommended,
        current_price_rub=current,
        delta_rub=delta,
        delta_percent=delta_percent,
        price_status=status,
        price_status_label=label,
        factors=tuple(factors),
        explanation=explanation,
    )


def evaluate_family_pricing(*, family: str) -> list[PlanPricingEvaluation]:
    plans = list(
        SubscriptionPlan.objects.filter(family=family).prefetch_related("features").order_by("sort_order", "level", "id")
    )
    return [evaluate_plan_pricing(plan) for plan in plans]
