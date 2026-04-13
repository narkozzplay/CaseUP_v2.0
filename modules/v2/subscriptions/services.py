from __future__ import annotations

from decimal import Decimal

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.system.services import is_module_enabled

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

FEATURE_ACCESS_OK = "ok"
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


def get_subscription_context(user, *, family: str = DEFAULT_SUBSCRIPTION_FAMILY) -> SubscriptionSummary:
    module_enabled = is_subscriptions_enabled()
    active_subscription = get_active_subscription(user, family=family)
    active_plan = active_subscription.plan if active_subscription is not None else None
    plan_features = [feature for feature in _get_plan_features(active_plan) if feature.is_active]
    all_active_features = list(
        SubscriptionFeature.objects.filter(is_active=True).order_by("sort_order", "name", "id")
    )

    if module_enabled:
        active_feature_codes = {
            feature.code
            for feature in plan_features
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

    if feature.code in active_summary.feature_codes:
        return FeatureAccessState(
            feature_code=feature.code,
            feature_name=feature.name,
            is_available=True,
            reason=FEATURE_ACCESS_OK,
            message="Доступ разрешён.",
            feature=feature,
            active_plan=active_summary.active_plan,
            required_plan=required_plan,
            module_key=feature.module_key,
            family=family,
        )

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


def get_subscription_purchase_quote(*, user, plan: SubscriptionPlan) -> SubscriptionPurchaseQuote:
    current_subscription = None
    current_plan = None

    if getattr(user, "is_authenticated", False):
        current_subscription = get_active_subscription(user, family=plan.family)
        current_plan = current_subscription.plan if current_subscription is not None else None

    plan_price = plan.price_rub or Decimal("0.00")

    if current_plan is None:
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=None,
            current_plan=None,
            charge_amount=plan_price,
            is_same_plan=False,
            is_upgrade=False,
            blocked_by_higher_level=False,
            message=f"Новый тариф: {plan_price:.2f} ₽ за {plan.billing_period_days} дн.",
        )

    if current_plan.id == plan.id:
        return SubscriptionPurchaseQuote(
            plan=plan,
            current_subscription=current_subscription,
            current_plan=current_plan,
            charge_amount=Decimal("0.00"),
            is_same_plan=True,
            is_upgrade=False,
            blocked_by_higher_level=False,
            message="Этот тариф уже активен у пользователя.",
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
        )

    raw_delta = plan_price - (current_plan.price_rub or Decimal("0.00"))
    upgrade_delta = raw_delta if raw_delta > Decimal("0.00") else Decimal("0.00")
    charge_amount = upgrade_delta + UPGRADE_SWITCH_FEE_RUB

    return SubscriptionPurchaseQuote(
        plan=plan,
        current_subscription=current_subscription,
        current_plan=current_plan,
        charge_amount=charge_amount,
        is_same_plan=False,
        is_upgrade=True,
        blocked_by_higher_level=False,
        message=(
            f"Апгрейд: {plan_price:.2f} ₽ - {(current_plan.price_rub or Decimal('0.00')):.2f} ₽ "
            f"+ {UPGRADE_SWITCH_FEE_RUB:.2f} ₽ = {charge_amount:.2f} ₽. "
            f"Срок начнётся заново на {plan.billing_period_days} дн."
        ),
    )

@transaction.atomic
def purchase_subscription_with_balance(*, user, plan: SubscriptionPlan):
    if not getattr(user, "is_authenticated", False):
        raise SubscriptionPurchaseError("Для покупки подписки нужно войти в аккаунт.")

    if not plan.is_active:
        raise SubscriptionPurchaseError("Этот тариф сейчас недоступен для покупки.")

    if plan.price_rub is None or plan.price_rub < 0:
        raise SubscriptionPurchaseError("У тарифа не настроена валидная цена для покупки.")

    _lock_subscription_owner(user)
    UserProfile.objects.select_for_update().get_or_create(user=user)

    quote = get_subscription_purchase_quote(user=user, plan=plan)

    if quote.is_same_plan:
        raise SubscriptionPurchaseError("Этот тариф уже активен у пользователя.")

    if quote.blocked_by_higher_level:
        raise SubscriptionPurchaseError("У вас действует подписка более высокого уровня.")

    charge_amount = quote.charge_amount
    if charge_amount <= 0:
        raise SubscriptionPurchaseError("Не удалось рассчитать стоимость покупки.")

    starts_at = timezone.now()
    expires_at = starts_at + timedelta(days=plan.billing_period_days)

    try:
        adjust_balance(
            user=user,
            delta=-charge_amount,
            reason=LedgerReason.SUBSCRIPTION_PURCHASE,
            amount_snapshot=charge_amount,
        )
    except BalanceError as exc:
        if str(exc) == "Insufficient balance":
            raise SubscriptionPurchaseError("Недостаточно средств на балансе профиля.") from exc
        raise SubscriptionPurchaseError(str(exc)) from exc

    purchase_note = "Purchased from profile balance"
    if quote.is_upgrade and quote.current_plan is not None:
        purchase_note = (
            f"Upgraded from {quote.current_plan.code} to {plan.code} "
            f"for {charge_amount:.2f} RUB (includes switch fee)"
        )

    return assign_user_subscription_plan(
        user=user,
        plan=plan,
        family=plan.family,
        starts_at=starts_at,
        expires_at=expires_at,
        notes=purchase_note,
    )
