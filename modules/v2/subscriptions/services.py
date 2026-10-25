from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.system.request_cache import request_memoize
from modules.v2.system.services import is_module_enabled

from .limits import FREE_LIMITS
from .models import SubscriptionFeature, SubscriptionFeaturePricingRule, SubscriptionPlan, SubscriptionPricingConfig, UserSubscription


@dataclass(frozen=True)
class SubscriptionSummary:
    active_subscription: UserSubscription | None
    active_plan: SubscriptionPlan | None
    feature_codes: tuple[str, ...]
    feature_map: dict[str, bool]
    has_active_subscription: bool
    plan_name: str
    plan_code: str
    plan_level: int
    module_enabled: bool
    family: str


@dataclass(frozen=True)
class FeatureAccessState:
    feature_code: str
    feature_name: str
    is_available: bool
    reason: str
    message: str
    feature: SubscriptionFeature | None
    active_plan: SubscriptionPlan | None
    required_plan: SubscriptionPlan | None
    module_key: str
    family: str

UPGRADE_SWITCH_FEE_RUB = Decimal("100.00")


@dataclass(frozen=True)
class SubscriptionPurchaseQuote:
    plan: SubscriptionPlan
    current_subscription: UserSubscription | None
    current_plan: SubscriptionPlan | None
    charge_amount: Decimal
    is_same_plan: bool
    is_upgrade: bool
    blocked_by_higher_level: bool
    message: str
    # Discount (percent) already included in charge_amount; full_amount is the price without it.
    # The larger of the referral discount and a pending promo code wins (discount_source).
    discount_percent: int = 0
    full_amount: Decimal | None = None
    discount_source: str = ""
    promo_activation_id: int | None = None
    months: int = 1
    period_discount_percent: int = 0
    is_renewal: bool = False
    expires_at: datetime | None = None

FEATURE_ACCESS_OK = "ok"
FEATURE_ACCESS_FREE = "free"
FEATURE_ACCESS_STAFF = "staff"
FEATURE_ACCESS_NOT_AUTHENTICATED = "not_authenticated"
FEATURE_ACCESS_NO_SUBSCRIPTION = "no_subscription"
FEATURE_ACCESS_UPGRADE_REQUIRED = "upgrade_required"
FEATURE_ACCESS_FEATURE_DISABLED = "feature_disabled"
FEATURE_ACCESS_MODULE_DISABLED = "module_disabled"
FEATURE_ACCESS_SUBSCRIPTIONS_DISABLED = "subscriptions_disabled"
FEATURE_ACCESS_UNKNOWN = "unknown_feature"

SUBSCRIPTIONS_MODULE_KEY = "subscriptions"
DEFAULT_SUBSCRIPTION_FAMILY = SubscriptionPlan.Family.SITE

FEATURES_PREFETCH = Prefetch(
    "features",
    queryset=SubscriptionFeature.objects.order_by("sort_order", "name", "id"),
)

UserModel = get_user_model()


class SubscriptionPurchaseError(ValueError):
    pass


class SubscriptionInvariantError(ValueError):
    pass


def _lock_subscription_owner(user) -> None:
    UserModel.objects.select_for_update().only("pk").get(pk=user.pk)


def _expire_stale_active_subscriptions(*, user, family: str, now: datetime) -> None:
    stale_qs = (
        UserSubscription.objects
        .select_for_update()
        .filter(
            user=user,
            status=UserSubscription.Status.ACTIVE,
            family=family,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
    )
    stale_ids = list(stale_qs.values_list("id", flat=True))
    if stale_ids:
        UserSubscription.objects.filter(id__in=stale_ids).update(
            status=UserSubscription.Status.EXPIRED,
            updated_at=now,
        )


def _get_locked_active_subscriptions(*, user, family: str) -> list[UserSubscription]:
    return list(
        UserSubscription.objects
        .select_for_update()
        .select_related("plan")
        .filter(
            user=user,
            status=UserSubscription.Status.ACTIVE,
            family=family,
        )
        .order_by("-starts_at", "-id")
    )


def get_available_plans(*, family: str | None = DEFAULT_SUBSCRIPTION_FAMILY) -> list[SubscriptionPlan]:
    queryset = SubscriptionPlan.objects.filter(is_active=True)
    if family:
        queryset = queryset.filter(family=family)
    return list(
        queryset
        .prefetch_related(FEATURES_PREFETCH)
        .order_by("sort_order", "level", "id")
    )


@request_memoize
def get_subscription_pricing_config() -> SubscriptionPricingConfig:
    config, _created = SubscriptionPricingConfig.objects.get_or_create(singleton_key=1)
    return config


def get_feature_pricing_rules(*, family: str) -> list[SubscriptionFeaturePricingRule]:
    return list(
        SubscriptionFeaturePricingRule.objects.filter(family=family)
        .select_related("feature", "required_plan")
        .order_by("sort_order", "id")
    )


@transaction.atomic
def sync_feature_rules_to_plans(*, family: str) -> None:
    plans = list(
        SubscriptionPlan.objects.filter(family=family).prefetch_related("features").order_by("sort_order", "level", "id")
    )
    rules = get_feature_pricing_rules(family=family)

    feature_ids_by_plan_id: dict[int, list[int]] = {plan.id: [] for plan in plans if plan.id}
    for rule in rules:
        if not rule.is_enabled or rule.required_plan is None:
            continue
        threshold_level = rule.required_plan.level
        for plan in plans:
            if plan.level >= threshold_level and plan.id is not None:
                feature_ids_by_plan_id.setdefault(plan.id, []).append(rule.feature_id)

    for plan in plans:
        if plan.id is None:
            continue
        plan.features.set(sorted(set(feature_ids_by_plan_id.get(plan.id, []))))


def is_subscriptions_enabled() -> bool:
    return is_module_enabled(SUBSCRIPTIONS_MODULE_KEY)


def get_active_subscription(user, *, family: str | None = DEFAULT_SUBSCRIPTION_FAMILY) -> UserSubscription | None:
    if not getattr(user, "is_authenticated", False):
        return None

    now = timezone.now()
    queryset = (
        UserSubscription.objects
        .select_related("plan")
        .prefetch_related("plan__features")
        .filter(
            user=user,
            status=UserSubscription.Status.ACTIVE,
            plan__is_active=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    if family:
        queryset = queryset.filter(family=family)
    return queryset.order_by("-starts_at", "-id").first()


def _get_plan_features(plan: SubscriptionPlan | None) -> list[SubscriptionFeature]:
    if plan is None:
        return []
    prefetched = getattr(plan, "_prefetched_objects_cache", {})
    if "features" in prefetched:
        return list(prefetched["features"])
    return list(plan.features.order_by("sort_order", "name", "id"))


@request_memoize
def get_subscription_context(user, *, family: str = DEFAULT_SUBSCRIPTION_FAMILY) -> SubscriptionSummary:
    module_enabled = is_subscriptions_enabled()
    active_subscription = get_active_subscription(user, family=family)
    active_plan = active_subscription.plan if active_subscription is not None else None
    plan_features = [feature for feature in _get_plan_features(active_plan) if feature.is_active]
    all_active_features = list(
        SubscriptionFeature.objects.filter(is_active=True).order_by("sort_order", "name", "id")
    )

    if module_enabled:
        is_authenticated = getattr(user, "is_authenticated", False)
        staff_bypass = _has_staff_bypass(user)
        granted = [
            feature
            for feature in all_active_features
            if staff_bypass
            or feature.free_tier == SubscriptionFeature.FreeTier.GUEST
            or (is_authenticated and feature.free_tier == SubscriptionFeature.FreeTier.REGISTERED)
        ]
        active_feature_codes = {
            feature.code
            for feature in [*plan_features, *granted]
            if not feature.module_key or is_module_enabled(feature.module_key)
        }
        feature_map = {
            feature.code: feature.code in active_feature_codes
            for feature in all_active_features
        }
        plan_name = active_plan.name if active_plan is not None else "Без подписки"
    else:
        active_feature_codes = {feature.code for feature in all_active_features}
        feature_map = {feature.code: True for feature in all_active_features}
        plan_name = "Модуль подписок выключен"

    return SubscriptionSummary(
        active_subscription=active_subscription,
        active_plan=active_plan,
        feature_codes=tuple(sorted(active_feature_codes)),
        feature_map=feature_map,
        has_active_subscription=active_subscription is not None,
        plan_name=plan_name,
        plan_code=active_plan.code if active_plan is not None else "",
        plan_level=int(active_plan.level) if active_plan is not None else 0,
        module_enabled=module_enabled,
        family=family,
    )


def get_required_plan_for_feature(feature_code: str, *, family: str = DEFAULT_SUBSCRIPTION_FAMILY) -> SubscriptionPlan | None:
    queryset = (
        SubscriptionPlan.objects
        .filter(is_active=True, features__code=feature_code, features__is_active=True)
        .distinct()
    )
    if family:
        queryset = queryset.filter(family=family)
    return queryset.order_by("sort_order", "level", "id").first()


def _has_staff_bypass(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return get_user_role(user).is_admin_plus


def _get_all_family_feature_codes(user, summary: SubscriptionSummary) -> set[str]:
    """Feature codes from the active plans of every family, so a farmer plan can unlock a shared feature like no_ads."""
    codes = set(summary.feature_codes)
    for family in SubscriptionPlan.Family.values:
        if family != summary.family:
            codes.update(get_subscription_context(user, family=family).feature_codes)
    return codes


def _granted_state(feature, summary, required_plan, family, reason, message) -> FeatureAccessState:
    return FeatureAccessState(
        feature_code=feature.code,
        feature_name=feature.name,
        is_available=True,
        reason=reason,
        message=message,
        feature=feature,
        active_plan=summary.active_plan,
        required_plan=required_plan,
        module_key=feature.module_key,
        family=family,
    )


def get_feature_access_state(user, feature_code: str, *, family: str = DEFAULT_SUBSCRIPTION_FAMILY) -> FeatureAccessState:
    normalized_code = (feature_code or "").strip()
    feature = SubscriptionFeature.objects.filter(code=normalized_code).first()
    active_summary = get_subscription_context(user, family=family)
    required_plan = get_required_plan_for_feature(normalized_code, family=family)

    if feature is None:
        return FeatureAccessState(
            feature_code=normalized_code,
            feature_name=normalized_code or "Unknown feature",
            is_available=False,
            reason=FEATURE_ACCESS_UNKNOWN,
            message="Запрошенная подписочная возможность не зарегистрирована.",
            feature=None,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key="",
            family=family,
        )

    if not feature.is_active:
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=False,
            reason=FEATURE_ACCESS_FEATURE_DISABLED,
            message="Эта возможность сейчас временно отключена.",
            feature=feature,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

    if feature.module_key and not is_module_enabled(feature.module_key):
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=False,
            reason=FEATURE_ACCESS_MODULE_DISABLED,
            message="Связанный модуль сейчас глобально отключён на уровне сайта.",
            feature=feature,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

    if not active_summary.module_enabled:
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=True,
            reason=FEATURE_ACCESS_SUBSCRIPTIONS_DISABLED,
            message="Коммерческие ограничения по подписке глобально отключены.",
            feature=feature,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

    if feature.free_tier == SubscriptionFeature.FreeTier.GUEST:
        return _granted_state(feature, active_summary, required_plan, family, FEATURE_ACCESS_FREE, "Доступно бесплатно.")

    if not getattr(user, "is_authenticated", False):
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=False,
            reason=FEATURE_ACCESS_NOT_AUTHENTICATED,
            message="Для доступа нужен вход в аккаунт.",
            feature=feature,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

    if feature.free_tier == SubscriptionFeature.FreeTier.REGISTERED:
        return _granted_state(feature, active_summary, required_plan, family, FEATURE_ACCESS_FREE, "Доступно бесплатно.")

    if _has_staff_bypass(user):
        return _granted_state(feature, active_summary, required_plan, family, FEATURE_ACCESS_STAFF, "Администрации доступно всё.")

    if feature.code in _get_all_family_feature_codes(user, active_summary):
        return _granted_state(feature, active_summary, required_plan, family, FEATURE_ACCESS_OK, "Доступ разрешён.")

    if active_summary.active_plan is None:
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=False,
            reason=FEATURE_ACCESS_NO_SUBSCRIPTION,
            message="Для этого раздела нужна активная подписка.",
            feature=feature,
            active_plan=None,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

    upgrade_message = "Ваша текущая подписка не включает эту возможность."
    if required_plan is not None:
        upgrade_message = f"Доступ открывается начиная с тарифа {required_plan.name}."

    return FeatureAccessState(
        feature_code=feature.code,
        feature_name=feature.name,
        is_available=False,
        reason=FEATURE_ACCESS_UPGRADE_REQUIRED,
        message=upgrade_message,
        feature=feature,
        active_plan=active_summary.active_plan,
        required_plan=required_plan,
        module_key=feature.module_key,
        family=family,
    )


def user_has_feature(user, feature_code: str, *, family: str = DEFAULT_SUBSCRIPTION_FAMILY) -> bool:
    return get_feature_access_state(user, feature_code, family=family).is_available


def get_user_limit(user, limit_key: str) -> int | None:
    """How many of limit_key the user may have; None means unlimited. The most generous active plan wins."""
    if _has_staff_bypass(user) or not is_subscriptions_enabled():
        return None
    best: int | None = FREE_LIMITS.get(limit_key)
    if best is None:
        return None
    for family in SubscriptionPlan.Family.values:
        subscription = get_active_subscription(user, family=family)
        if subscription is None:
            continue
        plan_limits = subscription.plan.limits or {}
        if limit_key not in plan_limits:
            continue
        value = plan_limits[limit_key]
        if value is None:
            return None
        best = max(best, int(value))
    return best


def is_within_limit(user, limit_key: str, used: int) -> bool:
    """True if the user may add one more item when they already have `used`."""
    limit = get_user_limit(user, limit_key)
    return limit is None or used < limit


@transaction.atomic
def assign_user_subscription_plan(
    *,
    user,
    plan: SubscriptionPlan | None,
    family: str | None = None,
    starts_at: datetime | None = None,
    expires_at: datetime | None = None,
    notes: str = "",
) -> UserSubscription | None:
    target_family = family or (plan.family if plan is not None else DEFAULT_SUBSCRIPTION_FAMILY)
    if plan is not None and family and family != plan.family:
        raise SubscriptionInvariantError("Requested subscription family does not match selected plan family.")
    _lock_subscription_owner(user)
    now = timezone.now()
    _expire_stale_active_subscriptions(user=user, family=target_family, now=now)
    active_subscriptions = _get_locked_active_subscriptions(user=user, family=target_family)
    if len(active_subscriptions) > 1:
        raise SubscriptionInvariantError(
            "Multiple active subscriptions detected for the same user and family."
        )

    current = active_subscriptions[0] if active_subscriptions else None
    starts_at = starts_at or now
    notes = (notes or "").strip()

    if current is not None:
        same_plan = plan is not None and current.plan_id == plan.id
        same_expiry = current.expires_at == expires_at
        if same_plan and same_expiry:
            if current.notes != notes:
                current.notes = notes
                current.save(update_fields=["notes", "updated_at"])
            return current

        current.status = UserSubscription.Status.CANCELED
        if current.expires_at is None or current.expires_at > now:
            current.expires_at = now
        current.save(update_fields=["status", "expires_at", "updated_at"])

    if plan is None:
        return None

    return UserSubscription.objects.create(
        user=user,
        plan=plan,
        family=target_family,
        status=UserSubscription.Status.ACTIVE,
        starts_at=starts_at,
        expires_at=expires_at,
        notes=notes,
    )


PERIOD_MONTHS = (1, 3, 6, 12)


def normalize_months(value) -> int:
    try:
        months = int(value)
    except (TypeError, ValueError):
        return 1
    return months if months in PERIOD_MONTHS else 1


def get_period_discount_percent(months: int) -> int:
    config = get_subscription_pricing_config()
    return {
        3: config.period_discount_3m_percent,
        6: config.period_discount_6m_percent,
        12: config.period_discount_12m_percent,
    }.get(months, 0)


def get_period_price(plan: SubscriptionPlan, months: int) -> Decimal:
    """Price for `months` billing periods with the long-term discount, rounded to whole rubles."""
    monthly = plan.price_rub or Decimal("0.00")
    discount = Decimal(get_period_discount_percent(months))
    total = monthly * months * (Decimal("100") - discount) / Decimal("100")
    return total.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))


def get_period_options(plan: SubscriptionPlan) -> list[dict]:
    options = []
    for months in PERIOD_MONTHS:
        price = get_period_price(plan, months)
        options.append({
            "months": months,
            "price": price,
            "per_month": (price / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "discount_percent": get_period_discount_percent(months),
        })
    return options


def _period_days(plan: SubscriptionPlan, months: int) -> int:
    return plan.billing_period_days * months


def _remaining_credit(subscription: UserSubscription, now: datetime) -> Decimal:
    """Unused value of the current subscription, counted at its monthly price."""
    monthly = subscription.plan.price_rub or Decimal("0.00")
    if subscription.expires_at is None:
        return monthly
    remaining_seconds = max((subscription.expires_at - now).total_seconds(), 0)
    period_seconds = subscription.plan.billing_period_days * 86400
    if period_seconds <= 0:
        return Decimal("0.00")
    credit = monthly * Decimal(str(remaining_seconds)) / Decimal(period_seconds)
    return credit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _months_label(months: int) -> str:
    return {1: "1 месяц", 3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"}.get(months, f"{months} мес.")


DISCOUNT_SOURCE_REFERRAL = "referral"
DISCOUNT_SOURCE_PROMO = "promo"


def _best_discount(user, family: str) -> tuple[int, str, int | None]:
    """(percent, source, promo activation id): the referral discount or a pending promo code, whichever is larger."""
    from modules.v2.promo.services import subscription_discount
    from modules.v2.referrals.services import subscription_discount_percent

    referral = subscription_discount_percent(user)
    promo, activation = subscription_discount(user, family)
    if promo > referral and activation is not None:
        return promo, DISCOUNT_SOURCE_PROMO, activation.pk
    if referral:
        return referral, DISCOUNT_SOURCE_REFERRAL, None
    return 0, "", None


def _discount_note(percent: int, source: str, full_amount: Decimal, charge_amount: Decimal) -> str:
    if not percent or charge_amount == full_amount:
        return ""
    label = "Скидка по промокоду" if source == DISCOUNT_SOURCE_PROMO else "Реферальная скидка"
    return f" {label} {percent}%: {charge_amount:.2f} ₽ вместо {full_amount:.2f} ₽."


def get_subscription_purchase_quote(*, user, plan: SubscriptionPlan, months: int = 1) -> SubscriptionPurchaseQuote:
    from modules.v2.referrals.services import apply_discount

    months = normalize_months(months)
    now = timezone.now()
    current_subscription = None
    current_plan = None
    discount, source, promo_activation_id = 0, "", None

    if getattr(user, "is_authenticated", False):
        current_subscription = get_active_subscription(user, family=plan.family)
        current_plan = current_subscription.plan if current_subscription is not None else None
        discount, source, promo_activation_id = _best_discount(user, plan.family)

    period_price = get_period_price(plan, months)
    period_discount = get_period_discount_percent(months)
    period_days = _period_days(plan, months)
    period_note = f" Срок: {_months_label(months)}" + (f", скидка {period_discount}%." if period_discount else ".")

    def discount_fields(full_amount: Decimal, charge_amount: Decimal) -> dict:
        applied = charge_amount != full_amount
        return {
            "discount_percent": discount if applied else 0,
            "full_amount": full_amount,
            "discount_source": source if applied else "",
            "promo_activation_id": promo_activation_id if applied else None,
            "months": months,
            "period_discount_percent": period_discount,
        }

    if current_plan is None:
        charge_amount = apply_discount(period_price, discount)
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=None,
            current_plan=None,
            charge_amount=charge_amount,
            is_same_plan=False,
            is_upgrade=False,
            blocked_by_higher_level=False,
            message=f"Новый тариф: {period_price:.2f} ₽ за {period_days} дн." + period_note + _discount_note(discount, source, period_price, charge_amount),
            expires_at=now + timedelta(days=period_days),
            **discount_fields(period_price, charge_amount),
        )

    if current_plan.id == plan.id and current_subscription.expires_at is None:
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=current_subscription,
            current_plan=current_plan,
            charge_amount=Decimal("0.00"),
            is_same_plan=True,
            is_upgrade=False,
            blocked_by_higher_level=False,
            message="Этот тариф уже активен у пользователя.",
            months=months,
        )

    if current_plan.id == plan.id:
        # Buying the active plan again extends it from its current end date.
        charge_amount = apply_discount(period_price, discount)
        base = current_subscription.expires_at if current_subscription.expires_at and current_subscription.expires_at > now else now
        expires_at = base + timedelta(days=period_days)
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=current_subscription,
            current_plan=current_plan,
            charge_amount=charge_amount,
            is_same_plan=True,
            is_upgrade=False,
            blocked_by_higher_level=False,
            is_renewal=True,
            message=(
                f"Продление: {period_price:.2f} ₽, тариф будет действовать до {timezone.localtime(expires_at):%d.%m.%Y}."
                + period_note + _discount_note(discount, source, period_price, charge_amount)
            ),
            expires_at=expires_at,
            **discount_fields(period_price, charge_amount),
        )

    if current_plan.level > plan.level:
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=current_subscription,
            current_plan=current_plan,
            charge_amount=Decimal("0.00"),
            is_same_plan=False,
            is_upgrade=False,
            blocked_by_higher_level=True,
            message="У вас действует подписка более высокого уровня.",
            months=months,
        )

    credit = _remaining_credit(current_subscription, now)
    raw_delta = period_price - credit
    upgrade_delta = raw_delta if raw_delta > Decimal("0.00") else Decimal("0.00")
    # The referral discount applies to the price difference, not to the fixed switch fee.
    full_amount = upgrade_delta + UPGRADE_SWITCH_FEE_RUB
    charge_amount = apply_discount(upgrade_delta, discount) + UPGRADE_SWITCH_FEE_RUB

    return SubscriptionPurchaseQuote(
        plan=plan,
        current_subscription=current_subscription,
        current_plan=current_plan,
        charge_amount=charge_amount,
        is_same_plan=False,
        is_upgrade=True,
        blocked_by_higher_level=False,
        message=(
            f"Апгрейд: {period_price:.2f} ₽ - {credit:.2f} ₽ (остаток текущего тарифа) "
            f"+ {UPGRADE_SWITCH_FEE_RUB:.2f} ₽ = {charge_amount:.2f} ₽. "
            f"Срок начнётся заново на {period_days} дн."
            + period_note + _discount_note(discount, source, full_amount, charge_amount)
        ),
        expires_at=now + timedelta(days=period_days),
        **discount_fields(full_amount, charge_amount),
    )

@transaction.atomic
def purchase_subscription_with_balance(*, user, plan: SubscriptionPlan, months: int = 1):
    if not getattr(user, "is_authenticated", False):
        raise SubscriptionPurchaseError("Для покупки подписки нужно войти в аккаунт.")

    if not plan.is_active:
        raise SubscriptionPurchaseError("Этот тариф сейчас недоступен для покупки.")

    if plan.price_rub is None or plan.price_rub < 0:
        raise SubscriptionPurchaseError("У тарифа не настроена валидная цена для покупки.")

    _lock_subscription_owner(user)
    UserProfile.objects.select_for_update().get_or_create(user=user)

    quote = get_subscription_purchase_quote(user=user, plan=plan, months=months)

    if quote.is_same_plan and not quote.is_renewal:
        raise SubscriptionPurchaseError("Этот тариф уже активен у пользователя.")

    if quote.blocked_by_higher_level:
        raise SubscriptionPurchaseError("У вас действует подписка более высокого уровня.")

    charge_amount = quote.charge_amount
    if charge_amount <= 0:
        raise SubscriptionPurchaseError("Не удалось рассчитать стоимость покупки.")

    starts_at = timezone.now()
    if quote.is_renewal and quote.expires_at is not None:
        expires_at = quote.expires_at
    else:
        expires_at = starts_at + timedelta(days=_period_days(plan, quote.months))

    try:
        ledger_entry = adjust_balance(
            user=user,
            delta=-charge_amount,
            reason=LedgerReason.SUBSCRIPTION_PURCHASE,
            amount_snapshot=charge_amount,
        )
    except BalanceError as exc:
        if str(exc) == "Insufficient balance":
            raise SubscriptionPurchaseError("Недостаточно средств на балансе профиля.") from exc
        raise SubscriptionPurchaseError(str(exc)) from exc

    from modules.v2.business.revenue import record_ledger_entry

    # The switch fee is booked as a commission, the rest as subscription income.
    record_ledger_entry(ledger_entry, switch_fee=UPGRADE_SWITCH_FEE_RUB if quote.is_upgrade else Decimal("0.00"))

    purchase_note = f"Purchased from profile balance for {quote.months} month(s)"
    if quote.is_renewal:
        purchase_note = f"Renewed for {quote.months} month(s), {charge_amount:.2f} RUB"
    if quote.is_upgrade and quote.current_plan is not None:
        purchase_note = (
            f"Upgraded from {quote.current_plan.code} to {plan.code} "
            f"for {charge_amount:.2f} RUB (includes switch fee)"
        )
    if quote.discount_percent:
        purchase_note += f"; {quote.discount_source} discount {quote.discount_percent}% (full price {quote.full_amount:.2f} RUB)"
    if quote.discount_source == DISCOUNT_SOURCE_PROMO and quote.promo_activation_id:
        from modules.v2.promo.services import use_subscription_discount

        use_subscription_discount(
            activation_id=quote.promo_activation_id,
            amount_saved=quote.full_amount - charge_amount,
            note=f"Подписка {plan.name}: {charge_amount:.2f} ₽ вместо {quote.full_amount:.2f} ₽",
        )

    if quote.is_renewal and quote.current_subscription is not None:
        subscription = UserSubscription.objects.select_for_update().get(pk=quote.current_subscription.pk)
        subscription.expires_at = expires_at
        subscription.expiry_reminder_sent_at = None
        subscription.notes = "\n".join(part for part in (subscription.notes, purchase_note) if part)
        subscription.save(update_fields=["expires_at", "expiry_reminder_sent_at", "notes", "updated_at"])
        return subscription

    return assign_user_subscription_plan(
        user=user,
        plan=plan,
        family=plan.family,
        starts_at=starts_at,
        expires_at=expires_at,
        notes=purchase_note,
    )
