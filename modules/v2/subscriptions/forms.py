from __future__ import annotations

from django import forms
from django.forms import modelformset_factory

from .limits import PRICE_ALERTS
from .models import SubscriptionFeaturePricingRule, SubscriptionPlan, SubscriptionPricingConfig


class SubscriptionPricingConfigForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPricingConfig
        fields = [
            "site_base_price_rub",
            "site_level_step_rub",
            "farmer_base_price_rub",
            "farmer_level_step_rub",
            "reduced_commission_reference_turnover_rub",
            "reduced_commission_capture_ratio_percent",
            "global_price_floor_rub",
            "reasonable_delta_percent",
            "period_discount_3m_percent",
            "period_discount_6m_percent",
            "period_discount_12m_percent",
            "trial_enabled",
            "trial_plan",
            "trial_price_rub",
            "trial_days",
        ]
        widgets = {
            **{
                field_name: forms.NumberInput(attrs={"class": "form-control", "step": "0.01"})
                for field_name in fields
            },
            "trial_enabled": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
            "trial_plan": forms.Select(attrs={"class": "form-control"}),
            "trial_days": forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "1", "max": "30"}),
        }
        labels = {
            "site_base_price_rub": "Базовая цена, ₽",
            "site_level_step_rub": "Надбавка за уровень, ₽",
            "farmer_base_price_rub": "Базовая цена, ₽",
            "farmer_level_step_rub": "Надбавка за уровень, ₽",
            "reduced_commission_reference_turnover_rub": "Типичный оборот фермера, ₽",
            "reduced_commission_capture_ratio_percent": "Доля выгоды в цене, %",
            "global_price_floor_rub": "Минимальная цена, ₽",
            "reasonable_delta_percent": "Допустимое отклонение, %",
            "period_discount_3m_percent": "Скидка за 3 месяца, %",
            "period_discount_6m_percent": "Скидка за 6 месяцев, %",
            "period_discount_12m_percent": "Скидка за 12 месяцев, %",
            "trial_enabled": "Пробный период включён",
            "trial_plan": "Тариф пробного периода",
            "trial_price_rub": "Цена пробного периода, ₽",
            "trial_days": "Длительность, дней",
        }
        help_texts = {
            "site_base_price_rub": "С неё начинается рекомендованная цена первого тарифа сайта.",
            "site_level_step_rub": "Сколько добавляется к рекомендованной цене за каждый следующий тариф сайта.",
            "farmer_base_price_rub": "С неё начинается рекомендованная цена первого тарифа фермы.",
            "farmer_level_step_rub": "Сколько добавляется за каждый следующий тариф фермы.",
            "reduced_commission_reference_turnover_rub": "Месячный оборот, по которому оценивается выгода от пониженной комиссии.",
            "reduced_commission_capture_ratio_percent": "Какую часть сэкономленной комиссии закладывать в цену тарифа.",
            "global_price_floor_rub": "Рекомендованная цена любого тарифа не опускается ниже этой суммы.",
            "reasonable_delta_percent": "Если цена отличается от рекомендованной не больше чем на столько, она «в норме».",
            "period_discount_3m_percent": "Цена 3 месяцев = 3 × цена тарифа минус эта скидка.",
            "period_discount_6m_percent": "Цена 6 месяцев = 6 × цена тарифа минус эта скидка.",
            "period_discount_12m_percent": "Цена года = 12 × цена тарифа минус эта скидка.",
            "trial_enabled": "Новый подписчик может взять тариф на пробу и согласиться на автопродление.",
            "trial_plan": "Пусто — тариф Плюс. После пробы каждый месяц списывается его цена.",
            "trial_price_rub": "Списывается с игрового баланса при оформлении.",
            "trial_days": "От 1 до 30 дней.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trial_plan"].queryset = SubscriptionPlan.objects.filter(family=SubscriptionPlan.Family.SITE, is_active=True).order_by("sort_order", "level")
        self.fields["trial_plan"].empty_label = "Плюс (по умолчанию)"


class SubscriptionPlanConfigForm(forms.ModelForm):
    ui_features_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        label="UI features",
        help_text="Одна строка = один пункт в карточке тарифа.",
    )

    price_alerts_limit = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "1"}),
        label="Ценовых алертов",
        help_text="Пусто — как в бесплатном доступе. Отметь «без лимита», чтобы снять ограничение.",
    )
    price_alerts_unlimited = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
        label="Алерты без лимита",
    )

    class Meta:
        model = SubscriptionPlan
        fields = [
            "code",
            "level",
            "audience",
            "name",
            "description",
            "ui_badge",
            "ui_features_text",
            "price_rub",
            "price_floor_rub",
            "manual_price_adjustment_rub",
            "billing_period_days",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "level": forms.NumberInput(attrs={"class": "form-control", "step": "1"}),
            "audience": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "ui_badge": forms.TextInput(attrs={"class": "form-control"}),
            "price_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "price_floor_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "manual_price_adjustment_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "billing_period_days": forms.NumberInput(attrs={"class": "form-control", "step": "1"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "step": "1"}),
            "is_active": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
        }

    def __init__(self, *args, family: str | None = None, **kwargs):
        self.family = family
        super().__init__(*args, **kwargs)
        self.fields["ui_features_text"].initial = "\n".join(self.instance.ui_feature_list)
        limits = self.instance.limits or {}
        if PRICE_ALERTS in limits:
            if limits[PRICE_ALERTS] is None:
                self.fields["price_alerts_unlimited"].initial = True
            else:
                self.fields["price_alerts_limit"].initial = limits[PRICE_ALERTS]

    def clean_ui_features_text(self):
        value = self.cleaned_data.get("ui_features_text", "")
        return [line.strip() for line in value.splitlines() if line.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.family:
            instance.family = self.family
        instance.ui_features = self.cleaned_data.get("ui_features_text", [])
        limits = dict(instance.limits or {})
        if self.cleaned_data.get("price_alerts_unlimited"):
            limits[PRICE_ALERTS] = None
        elif self.cleaned_data.get("price_alerts_limit") is not None:
            limits[PRICE_ALERTS] = self.cleaned_data["price_alerts_limit"]
        else:
            limits.pop(PRICE_ALERTS, None)
        instance.limits = limits
        if commit:
            instance.save()
        return instance


class SubscriptionFeaturePricingRuleForm(forms.ModelForm):
    class Meta:
        model = SubscriptionFeaturePricingRule
        fields = ["feature", "required_plan", "value_rub", "sort_order", "is_enabled"]
        widgets = {
            "feature": forms.Select(attrs={"class": "form-control"}),
            "required_plan": forms.Select(attrs={"class": "form-control"}),
            "value_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "step": "1"}),
            "is_enabled": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
        }

    def __init__(self, *args, family: str | None = None, **kwargs):
        self.family = family
        super().__init__(*args, **kwargs)
        feature_queryset = self.fields["feature"].queryset.filter(is_active=True).order_by("sort_order", "name", "id")
        self.fields["feature"].queryset = feature_queryset
        if family:
            self.fields["required_plan"].queryset = SubscriptionPlan.objects.filter(family=family).order_by("sort_order", "level", "id")
        else:
            self.fields["required_plan"].queryset = SubscriptionPlan.objects.order_by("family", "sort_order", "level", "id")

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.family:
            instance.family = self.family
        if commit:
            instance.save()
        return instance


SitePlanPricingFormSet = modelformset_factory(
    SubscriptionPlan,
    form=SubscriptionPlanConfigForm,
    extra=1,
)

FarmerPlanPricingFormSet = modelformset_factory(
    SubscriptionPlan,
    form=SubscriptionPlanConfigForm,
    extra=1,
)

SiteFeaturePricingRuleFormSet = modelformset_factory(
    SubscriptionFeaturePricingRule,
    form=SubscriptionFeaturePricingRuleForm,
    extra=1,
)

FarmerFeaturePricingRuleFormSet = modelformset_factory(
    SubscriptionFeaturePricingRule,
    form=SubscriptionFeaturePricingRuleForm,
    extra=1,
)
