from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import (
    FarmerFeaturePricingRuleFormSet,
    FarmerPlanPricingFormSet,
    SiteFeaturePricingRuleFormSet,
    SitePlanPricingFormSet,
    SubscriptionPricingConfigForm,
)
from .limits import FREE_LIMITS, PRICE_ALERTS
from .models import SubscriptionFeature, SubscriptionFeaturePricingRule, SubscriptionPlan
from .pricing import evaluate_family_pricing
from .services import (
    SubscriptionPurchaseError,
    PERIOD_MONTHS,
    get_available_plans,
    get_period_discount_percent,
    get_period_options,
    get_period_price,
    normalize_months,
    get_subscription_context,
    get_subscription_pricing_config,
    get_subscription_purchase_quote,
    purchase_subscription_with_balance,
    sync_feature_rules_to_plans,
)
from .trial import TrialError, set_auto_renew, start_trial, trial_offer


FARMER_FAMILY = SubscriptionPlan.Family.FARMER
SITE_FAMILY = SubscriptionPlan.Family.SITE


def _get_viewer_balance(user):
    if not getattr(user, "is_authenticated", False):
        return None
    profile = getattr(user, "profile_v2", None)
    return getattr(profile, "balance_coins", None)


def _purchase_redirect_name(family: str) -> str:
    return "subscriptions:farmer_plans" if family == FARMER_FAMILY else "subscriptions:plans"


def _attach_purchase_ui(*, plans, user, viewer_balance, months=1):
    is_authenticated = getattr(user, "is_authenticated", False)

    for plan in plans:
        quote = get_subscription_purchase_quote(user=user, plan=plan, months=months)
        plan.period_options = get_period_options(plan)
        plan.period_price = get_period_price(plan, quote.months)
        plan.period_per_month = (plan.period_price / quote.months).quantize(Decimal("0.01"))

        purchase_ui = {
            "state": "buy",
            "button_label": "Купить",
            "hint": quote.message,
            "quote_amount": quote.charge_amount,
            "is_disabled": False,
            "href": "",
            "show_amount": True,
        }

        if not is_authenticated:
            purchase_ui.update(
                {
                    "state": "login",
                    "button_label": "Войти для покупки",
                    "hint": "Нужен вход в аккаунт, чтобы списать средства с профиля.",
                    "href": reverse("accounts:login"),
                    "show_amount": False,
                }
            )
        elif quote.is_same_plan and not quote.is_renewal:
            purchase_ui.update(
                {
                    "state": "current",
                    "button_label": "Активен сейчас",
                    "hint": "Этот тариф уже действует бессрочно.",
                    "is_disabled": True,
                    "show_amount": False,
                }
            )
        elif quote.is_renewal and viewer_balance is not None and viewer_balance < quote.charge_amount:
            purchase_ui.update(
                {
                    "state": "topup",
                    "button_label": "Пополнить баланс",
                    "hint": (
                        f"Для продления нужно {quote.charge_amount:.2f} ₽, "
                        f"на балансе {viewer_balance:.2f} ₽."
                    ),
                    "href": reverse("payments:topup"),
                    "show_amount": True,
                }
            )
        elif quote.is_renewal:
            purchase_ui.update(
                {
                    "state": "renew",
                    "button_label": "Продлить",
                    "hint": quote.message,
                    "show_amount": True,
                }
            )
        elif quote.blocked_by_higher_level:
            purchase_ui.update(
                {
                    "state": "blocked_higher",
                    "button_label": "Недоступно",
                    "hint": "У вас действует подписка более высокого уровня.",
                    "is_disabled": True,
                    "show_amount": False,
                }
            )
        elif viewer_balance is not None and viewer_balance < quote.charge_amount:
            purchase_ui.update(
                {
                    "state": "topup",
                    "button_label": "Пополнить баланс",
                    "hint": (
                        f"Для покупки нужно {quote.charge_amount:.2f} ₽, "
                        f"на балансе {viewer_balance:.2f} ₽."
                    ),
                    "href": reverse("payments:topup"),
                    "show_amount": True,
                }
            )
        else:
            purchase_ui.update(
                {
                    "state": "buy",
                    "button_label": "Купить",
                    "hint": quote.message,
                    "show_amount": True,
                }
            )

        plan.purchase_quote = quote
        plan.purchase_ui = purchase_ui

    return plans


def _build_comparison_features(plans, *, free_features=()):
    plan_feature_sets = {
        plan.id: {feature.code for feature in plan.features.all() if feature.is_active}
        for plan in plans
    }
    comparison_features = [
        {"feature": feature, "plan_ids": tuple(plan.id for plan in plans), "is_free": True}
        for feature in free_features
    ]
    seen_feature_ids = {feature.id for feature in free_features}
    for plan in plans:
        for feature in plan.features.all():
            if not feature.is_active or feature.id in seen_feature_ids:
                continue
            seen_feature_ids.add(feature.id)
            comparison_features.append(
                {
                    "feature": feature,
                    "plan_ids": tuple(
                        candidate.id
                        for candidate in plans
                        if feature.code in plan_feature_sets.get(candidate.id, set())
                    ),
                    "is_free": False,
                }
            )
    return comparison_features


def _selected_months(request) -> int:
    return normalize_months(request.GET.get("months"))


def _period_tabs(selected: int) -> list[dict]:
    return [
        {"months": months, "discount_percent": get_period_discount_percent(months), "is_active": months == selected}
        for months in PERIOD_MONTHS
    ]


def plans_page(request):
    months = _selected_months(request)
    plans = get_available_plans(family=SITE_FAMILY)
    current_subscription = get_subscription_context(getattr(request, "user", None), family=SITE_FAMILY)
    current_farmer_subscription = get_subscription_context(getattr(request, "user", None), family=FARMER_FAMILY)
    viewer_balance = _get_viewer_balance(getattr(request, "user", None))
    plans = _attach_purchase_ui(plans=plans, user=getattr(request, "user", None), viewer_balance=viewer_balance, months=months)

    next_plan = None
    if current_subscription.active_plan is not None:
        next_plan = next(
            (plan for plan in plans if plan.level > current_subscription.plan_level),
            None,
        )

    free_features = list(
        SubscriptionFeature.objects
        .filter(is_active=True)
        .exclude(free_tier=SubscriptionFeature.FreeTier.NONE)
        .order_by("sort_order", "name", "id")
    )

    context = {
        "plans": plans,
        "comparison_features": _build_comparison_features(plans, free_features=free_features),
        "free_alert_limit": FREE_LIMITS[PRICE_ALERTS],
        "selected_months": months,
        "period_tabs": _period_tabs(months),
        "current_subscription": current_subscription,
        "current_farmer_subscription": current_farmer_subscription,
        "next_plan": next_plan,
        "role_access": get_user_role(getattr(request, "user", None)),
        "viewer_balance": viewer_balance,
        "trial": trial_offer(getattr(request, "user", None)),
    }
    return render(request, "subscriptions/plans.html", context)


def farmer_plans_page(request):
    months = _selected_months(request)
    plans = get_available_plans(family=FARMER_FAMILY)
    role_access = get_user_role(getattr(request, "user", None))
    current_subscription = get_subscription_context(getattr(request, "user", None), family=FARMER_FAMILY)
    current_site_subscription = get_subscription_context(getattr(request, "user", None), family=SITE_FAMILY)
    pricing_rows = evaluate_family_pricing(family=FARMER_FAMILY)
    viewer_balance = _get_viewer_balance(getattr(request, "user", None))
    plans = _attach_purchase_ui(plans=plans, user=getattr(request, "user", None), viewer_balance=viewer_balance, months=months)

    context = {
        "plans": plans,
        "comparison_features": _build_comparison_features(plans),
        "current_subscription": current_subscription,
        "current_site_subscription": current_site_subscription,
        "pricing_rows": pricing_rows,
        "role_access": role_access,
        "show_pricing_meta": role_access.is_admin_plus,
        "selected_months": months,
        "period_tabs": _period_tabs(months),
        "viewer_balance": viewer_balance,
        "free_access_items": (
            "Дашборд фермы, аккаунты и недельные дропы доступны без farmer-подписки.",
            "Базовый обзор активного пула и последних сессий доступен без глубокой аналитики.",
            "Farmer открывает аналитику фермы и TG-алерты, Farmer Pro добавляет сниженную комиссию, аналитику продаж и бонусную программу. Оба тарифа убирают рекламу.",
        ),
    }
    return render(request, "subscriptions/farmer_plans.html", context)


@login_required
def purchase_plan_with_balance_view(request, plan_id: int):
    if request.method != "POST":
        return redirect("subscriptions:plans")

    plan = get_object_or_404(SubscriptionPlan, pk=plan_id, is_active=True)
    months = normalize_months(request.POST.get("months"))

    try:
        purchase_subscription_with_balance(user=request.user, plan=plan, months=months)
    except SubscriptionPurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Тариф {plan.name} успешно активирован на {months} мес.")

    url = reverse(_purchase_redirect_name(plan.family))
    return redirect(f"{url}?months={months}" if months != 1 else url)


@login_required
def start_trial_view(request):
    if request.method != "POST":
        return redirect("subscriptions:plans")
    try:
        subscription = start_trial(request.user, accepted_auto_renew=request.POST.get("accept_auto_renew") == "1")
    except TrialError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Пробный период «{subscription.plan.name}» активен до {timezone.localtime(subscription.expires_at):%d.%m.%Y}. "
            "За 3 дня до списания придёт напоминание.",
        )
    return redirect("subscriptions:plans")


@login_required
def auto_renew_view(request):
    if request.method != "POST":
        return redirect("subscriptions:plans")
    enabled = request.POST.get("enabled") == "1"
    if set_auto_renew(request.user, enabled=enabled) is None:
        messages.error(request, "Нет подписки со сроком, для которой можно настроить автопродление.")
    elif enabled:
        messages.success(request, "Автопродление включено: перед окончанием срока цена месяца спишется с баланса.")
    else:
        messages.success(request, "Автопродление отключено. Подписка будет действовать до конца оплаченного срока.")
    return redirect("subscriptions:plans")


@login_required
def balance_topup_placeholder_view(request):
    # Old entry point, kept so existing links keep working.
    return redirect("payments:topup")


def _bind_family_formset(formset_class, *, request=None, queryset, prefix: str, family: str):
    kwargs = {
        "queryset": queryset,
        "prefix": prefix,
        "form_kwargs": {"family": family},
    }
    if request is not None:
        kwargs["data"] = request.POST
    return formset_class(**kwargs)


PRICING_TABS = (
    ("overview", "Обзор"),
    ("formula", "Формула цены"),
    ("site", "Тарифы сайта"),
    ("farmer", "Тарифы фермы"),
)
# Which tab holds the form for each POST scope: after saving (or on an error) the admin stays there.
PRICING_SCOPE_TABS = {
    "config": "formula",
    "site_plans": "site",
    "site_rules": "site",
    "farmer_plans": "farmer",
    "farmer_rules": "farmer",
}


def _plan_cards(plan_formset, pricing_rows):
    """Pair every plan form with its pricing evaluation (the extra blank form has none)."""
    by_plan = {row.plan.pk: row for row in pricing_rows}
    return [(form, by_plan.get(form.instance.pk)) for form in plan_formset]


def _build_pricing_dashboard_context(*, role_access, config_form, site_plan_formset, farmer_plan_formset, site_rule_formset, farmer_rule_formset, tab="overview"):
    site_rows = evaluate_family_pricing(family=SITE_FAMILY)
    farmer_rows = evaluate_family_pricing(family=FARMER_FAMILY)
    all_rows = [*site_rows, *farmer_rows]
    return {
        "config_form": config_form,
        "site_plan_formset": site_plan_formset,
        "farmer_plan_formset": farmer_plan_formset,
        "site_rule_formset": site_rule_formset,
        "farmer_rule_formset": farmer_rule_formset,
        "site_pricing_rows": site_rows,
        "farmer_pricing_rows": farmer_rows,
        "site_plan_cards": _plan_cards(site_plan_formset, site_rows),
        "farmer_plan_cards": _plan_cards(farmer_plan_formset, farmer_rows),
        "pricing_tab": tab,
        "pricing_tabs": PRICING_TABS,
        "pricing_summary": {
            "site": len(site_rows),
            "farmer": len(farmer_rows),
            "reasonable": sum(1 for row in all_rows if row.price_status == "reasonable"),
            "attention": sum(1 for row in all_rows if row.price_status != "reasonable"),
        },
        "role_access": role_access,
    }


@login_required
def pricing_dashboard_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Pricing dashboard доступен только admin+.")

    config = get_subscription_pricing_config()
    site_plans_qs = SubscriptionPlan.objects.filter(family=SITE_FAMILY).order_by("sort_order", "level", "id")
    farmer_plans_qs = SubscriptionPlan.objects.filter(family=FARMER_FAMILY).order_by("sort_order", "level", "id")
    site_rules_qs = SubscriptionFeaturePricingRule.objects.filter(family=SITE_FAMILY).select_related("feature", "required_plan").order_by("sort_order", "id")
    farmer_rules_qs = SubscriptionFeaturePricingRule.objects.filter(family=FARMER_FAMILY).select_related("feature", "required_plan").order_by("sort_order", "id")

    config_form = SubscriptionPricingConfigForm(instance=config)
    site_plan_formset = _bind_family_formset(SitePlanPricingFormSet, queryset=site_plans_qs, prefix="site-plans", family=SITE_FAMILY)
    farmer_plan_formset = _bind_family_formset(FarmerPlanPricingFormSet, queryset=farmer_plans_qs, prefix="farmer-plans", family=FARMER_FAMILY)
    site_rule_formset = _bind_family_formset(SiteFeaturePricingRuleFormSet, queryset=site_rules_qs, prefix="site-rules", family=SITE_FAMILY)
    farmer_rule_formset = _bind_family_formset(FarmerFeaturePricingRuleFormSet, queryset=farmer_rules_qs, prefix="farmer-rules", family=FARMER_FAMILY)

    tab = request.GET.get("tab") or "overview"
    if tab not in dict(PRICING_TABS):
        tab = "overview"
    tab_url = f"{reverse('subscriptions:pricing_dashboard')}?tab={{}}"

    if request.method == "POST":
        form_scope = request.POST.get("form_scope")
        tab = PRICING_SCOPE_TABS.get(form_scope, tab)

        if form_scope == "config":
            config_form = SubscriptionPricingConfigForm(request.POST, instance=config)
            if config_form.is_valid():
                config_form.save()
                messages.success(request, "Формула цены сохранена.")
                return redirect(tab_url.format("formula"))

        elif form_scope == "site_plans":
            site_plan_formset = _bind_family_formset(SitePlanPricingFormSet, request=request, queryset=site_plans_qs, prefix="site-plans", family=SITE_FAMILY)
            if site_plan_formset.is_valid():
                instances = site_plan_formset.save(commit=False)
                for instance in instances:
                    instance.family = SITE_FAMILY
                    instance.save()
                sync_feature_rules_to_plans(family=SITE_FAMILY)
                messages.success(request, "Тарифы сайта сохранены.")
                return redirect(tab_url.format("site"))

        elif form_scope == "farmer_plans":
            farmer_plan_formset = _bind_family_formset(FarmerPlanPricingFormSet, request=request, queryset=farmer_plans_qs, prefix="farmer-plans", family=FARMER_FAMILY)
            if farmer_plan_formset.is_valid():
                instances = farmer_plan_formset.save(commit=False)
                for instance in instances:
                    instance.family = FARMER_FAMILY
                    instance.save()
                sync_feature_rules_to_plans(family=FARMER_FAMILY)
                messages.success(request, "Тарифы фермы сохранены.")
                return redirect(tab_url.format("farmer"))

        elif form_scope == "site_rules":
            site_rule_formset = _bind_family_formset(SiteFeaturePricingRuleFormSet, request=request, queryset=site_rules_qs, prefix="site-rules", family=SITE_FAMILY)
            if site_rule_formset.is_valid():
                instances = site_rule_formset.save(commit=False)
                for instance in instances:
                    instance.family = SITE_FAMILY
                    instance.save()
                sync_feature_rules_to_plans(family=SITE_FAMILY)
                messages.success(request, "Функции тарифов сайта сохранены.")
                return redirect(tab_url.format("site"))

        elif form_scope == "farmer_rules":
            farmer_rule_formset = _bind_family_formset(FarmerFeaturePricingRuleFormSet, request=request, queryset=farmer_rules_qs, prefix="farmer-rules", family=FARMER_FAMILY)
            if farmer_rule_formset.is_valid():
                instances = farmer_rule_formset.save(commit=False)
                for instance in instances:
                    instance.family = FARMER_FAMILY
                    instance.save()
                sync_feature_rules_to_plans(family=FARMER_FAMILY)
                messages.success(request, "Функции тарифов фермы сохранены.")
                return redirect(tab_url.format("farmer"))

    context = _build_pricing_dashboard_context(
        role_access=role_access,
        config_form=config_form,
        site_plan_formset=site_plan_formset,
        farmer_plan_formset=farmer_plan_formset,
        site_rule_formset=site_rule_formset,
        farmer_rule_formset=farmer_rule_formset,
        tab=tab,
    )
    return render_dashboard_page(
        request,
        full_template="subscriptions/pricing_dashboard.html",
        partial_template="subscriptions/partials/pricing_dashboard_content.html",
        context=context,
        page_title=build_page_title("Цены подписок"),
        active_menu_key="subscription_pricing",
    )
