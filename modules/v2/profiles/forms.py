from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from modules.v2.subscriptions.models import SubscriptionPlan

User = get_user_model()


class _AdminBaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        textarea_fields = {
            "site_subscription_notes",
            "farmer_subscription_notes",
        }
        datetime_fields = {
            "site_subscription_expires_at",
            "farmer_subscription_expires_at",
        }

        for field_name, field in self.fields.items():
            if field_name in textarea_fields or field_name in datetime_fields:
                continue
            field.widget.attrs.setdefault("class", "form-control")


class AdminProfileIdentityForm(_AdminBaseForm):
    username = forms.CharField(max_length=150, label="Username")
    balance_coins = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Balance",
    )

    def __init__(self, *args, target_user=None, target_profile=None, **kwargs):
        self.target_user = target_user
        self.target_profile = target_profile
        super().__init__(*args, **kwargs)

        if target_user is not None and not self.is_bound:
            self.initial.setdefault("username", target_user.username)

        if target_profile is not None and not self.is_bound:
            self.initial.setdefault("balance_coins", target_profile.balance_coins)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username:
            raise forms.ValidationError("Username is required.")

        qs = User.objects.filter(username=username)
        if self.target_user is not None:
            qs = qs.exclude(pk=self.target_user.pk)

        if qs.exists():
            raise forms.ValidationError("Пользователь с таким username уже существует.")

        return username


class AdminSiteSubscriptionForm(_AdminBaseForm):
    site_subscription_plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.filter(
            is_active=True,
            family=SubscriptionPlan.Family.SITE,
        ).order_by("sort_order", "level", "id"),
        required=False,
        empty_label="Без site-подписки",
        label="Site subscription plan",
    )
    site_subscription_expires_at = forms.DateTimeField(
        required=False,
        label="Site subscription expires at",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-control"}
        ),
    )
    site_subscription_notes = forms.CharField(
        required=False,
        label="Site subscription notes",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args, active_site_subscription=None, **kwargs):
        self.active_site_subscription = active_site_subscription
        super().__init__(*args, **kwargs)

        if active_site_subscription is not None and not self.is_bound:
            self.initial.setdefault("site_subscription_plan", active_site_subscription.plan_id)
            if active_site_subscription.expires_at:
                self.initial.setdefault(
                    "site_subscription_expires_at",
                    active_site_subscription.expires_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
                )
            self.initial.setdefault("site_subscription_notes", active_site_subscription.notes)

    def clean(self):
        cleaned_data = super().clean()
        plan = cleaned_data.get("site_subscription_plan")
        expires_at = cleaned_data.get("site_subscription_expires_at")

        if expires_at and plan is None:
            self.add_error("site_subscription_plan", "Сначала выбери site-тариф.")

        return cleaned_data


class AdminFarmerSubscriptionForm(_AdminBaseForm):
    farmer_subscription_plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.filter(
            is_active=True,
            family=SubscriptionPlan.Family.FARMER,
        ).order_by("sort_order", "level", "id"),
        required=False,
        empty_label="Без farmer-подписки",
        label="Farmer subscription plan",
    )
    farmer_subscription_expires_at = forms.DateTimeField(
        required=False,
        label="Farmer subscription expires at",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-control"}
        ),
    )
    farmer_subscription_notes = forms.CharField(
        required=False,
        label="Farmer subscription notes",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args, active_farmer_subscription=None, **kwargs):
        self.active_farmer_subscription = active_farmer_subscription
        super().__init__(*args, **kwargs)

        if active_farmer_subscription is not None and not self.is_bound:
            self.initial.setdefault("farmer_subscription_plan", active_farmer_subscription.plan_id)
            if active_farmer_subscription.expires_at:
                self.initial.setdefault(
                    "farmer_subscription_expires_at",
                    active_farmer_subscription.expires_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
                )
            self.initial.setdefault("farmer_subscription_notes", active_farmer_subscription.notes)

    def clean(self):
        cleaned_data = super().clean()
        plan = cleaned_data.get("farmer_subscription_plan")
        expires_at = cleaned_data.get("farmer_subscription_expires_at")

        if expires_at and plan is None:
            self.add_error("farmer_subscription_plan", "Сначала выбери farmer-тариф.")

        return cleaned_data