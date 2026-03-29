from __future__ import annotations

from django import forms
from django.forms import modelformset_factory

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
        ]
        widgets = {
            field_name: forms.NumberInput(attrs={"class": "form-control", "step": "0.01"})
            for field_name in fields
        }
        labels = {
            "site_base_price_rub": "Site base price, ₽",
            "site_level_step_rub": "Site level step, ₽",
            "farmer_base_price_rub": "Farmer base price, ₽",
            "farmer_level_step_rub": "Farmer level step, ₽",
            "reduced_commission_reference_turnover_rub": "Reference turnover, ₽",
            "reduced_commission_capture_ratio_percent": "Capture ratio, %",
            "global_price_floor_rub": "Global floor, ₽",
            "reasonable_delta_percent": "Reasonable delta, %",
        }
        help_texts = {
            "site_base_price_rub": "Стартовая часть формулы для всей site-линейки.",
            "site_level_step_rub": "Сколько автоматически добавляется за каждый следующий site-уровень.",
            "farmer_base_price_rub": "Стартовая часть формулы для farmer-линейки.",
            "farmer_level_step_rub": "Надбавка за следующий farmer-план внутри линейки.",
            "reduced_commission_reference_turnover_rub": "Опорный оборот для расчета ценности reduced commission.",
            "reduced_commission_capture_ratio_percent": "Какая доля экономии на комиссии переводится в рекомендованную цену.",
            "global_price_floor_rub": "Нижняя граница рекомендованной цены для всех тарифов.",
            "reasonable_delta_percent": "Порог, в пределах которого цена считается нормальной.",
        }


class SubscriptionPlanConfigForm(forms.ModelForm):
    ui_features_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        label="UI features",
        help_text="Одна строка = один пункт в карточке тарифа.",
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

    def clean_ui_features_text(self):
        value = self.cleaned_data.get("ui_features_text", "")
        return [line.strip() for line in value.splitlines() if line.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.family:
            instance.family = self.family
        instance.ui_features = self.cleaned_data.get("ui_features_text", [])
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
