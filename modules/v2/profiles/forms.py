from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.store.services import get_user_theme_options_by_scope
from modules.v2.system.theme_plugins import get_theme_scope_label

from .models import UserRole
from .rbac import ROLE_RANK, get_user_role

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
    role = forms.ChoiceField(
        choices=UserRole.choices,
        label="Role",
    )
    is_store_author = forms.BooleanField(
        required=False,
        label="Store author",
    )

    def __init__(self, *args, target_user=None, target_profile=None, actor_user=None, **kwargs):
        self.target_user = target_user
        self.target_profile = target_profile
        self.actor_user = actor_user
        super().__init__(*args, **kwargs)

        if target_user is not None and not self.is_bound:
            self.initial.setdefault("username", target_user.username)

        if target_profile is not None and not self.is_bound:
            self.initial.setdefault("balance_coins", target_profile.balance_coins)
            self.initial.setdefault("role", target_profile.role)
            self.initial.setdefault("is_store_author", bool(target_profile.is_store_author))

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

    def clean_role(self):
        role = (self.cleaned_data.get("role") or "").strip()
        valid_roles = {choice for choice, _label in UserRole.choices}
        if role not in valid_roles:
            raise forms.ValidationError("Недопустимая роль.")

        actor = self.actor_user
        if not getattr(actor, "is_authenticated", False):
            raise forms.ValidationError("Только admin+ может менять роль.")

        actor_access = get_user_role(actor)
        actor_rank = actor_access.rank
        requested_rank = ROLE_RANK.get(role, ROLE_RANK[UserRole.USER])
        if requested_rank > actor_rank:
            raise forms.ValidationError("Нельзя назначить роль выше собственного уровня доступа.")

        return role


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


class UserThemePreferencesForm(forms.Form):
    FIELD_PREFIX = "theme_scope__"

    def __init__(self, *args, user=None, current_preferences=None, **kwargs):
        self.user = user
        self.current_preferences = dict(current_preferences or {})
        self.theme_options_by_scope = get_user_theme_options_by_scope(user)
        super().__init__(*args, **kwargs)

        if not self.theme_options_by_scope:
            self.theme_options_by_scope = {"global": []}

        for scope, themes in self.theme_options_by_scope.items():
            field_name = self._field_name(scope)
            choices = [("", "Site default / inherit")]
            for theme in themes:
                choices.append((theme.key, theme.name))

            self.fields[field_name] = forms.ChoiceField(
                required=False,
                choices=choices,
                label=get_theme_scope_label(scope),
                widget=forms.Select(attrs={"class": "form-control"}),
            )
            if not self.is_bound:
                self.initial[field_name] = self.current_preferences.get(scope, "")

    @classmethod
    def _field_name(cls, scope: str) -> str:
        return f"{cls.FIELD_PREFIX}{scope}"

    def clean(self):
        cleaned_data = super().clean()
        selected: dict[str, str] = {}
        for scope, themes in self.theme_options_by_scope.items():
            field_name = self._field_name(scope)
            value = str(cleaned_data.get(field_name) or "").strip().lower()
            if not value:
                continue
            allowed_keys = {theme.key for theme in themes}
            if value not in allowed_keys:
                self.add_error(field_name, "Тема недоступна для выбранного scope.")
                continue
            selected[scope] = value

        self.cleaned_preferences = selected if not self.errors else {}
        return cleaned_data

    def get_selected_preferences(self) -> dict[str, str]:
        return dict(getattr(self, "cleaned_preferences", {}))
