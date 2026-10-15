from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from modules.v2.case_simulator.models import SimCase

from .models import PromoCode
from .services import normalize_code

_DT_FORMAT = "%Y-%m-%dT%H:%M"


def _dt_widget():
    return forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}, format=_DT_FORMAT)


class PromoCodeForm(forms.ModelForm):
    owner_username = forms.CharField(
        label="Блогер (логин или ID)", required=False, max_length=150,
        help_text="Новые аккаунты, активировавшие код, станут его рефералами. Пусто — обычный промокод.",
    )

    class Meta:
        model = PromoCode
        fields = (
            "code", "kind", "title", "amount", "percent", "min_topup", "max_bonus", "subscription_family", "case",
            "max_activations", "starts_at", "expires_at", "only_new_accounts", "only_without_topups", "is_active",
        )
        labels = {
            "code": "Код",
            "kind": "Что даёт",
            "title": "Заметка",
            "amount": "Сумма бонуса, ₽",
            "percent": "Процент",
            "min_topup": "Минимальное пополнение, ₽",
            "max_bonus": "Максимальный бонус, ₽",
            "subscription_family": "Тарифы",
            "case": "Кейс",
            "max_activations": "Лимит активаций",
            "starts_at": "Начало действия",
            "expires_at": "Окончание действия",
            "only_new_accounts": "Только новые аккаунты",
            "only_without_topups": "Только без пополнений",
            "is_active": "Включён",
        }
        widgets = {"starts_at": _dt_widget(), "expires_at": _dt_widget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Terminal cases have their own flow; a free opening applies to ordinary cases only.
        self.fields["case"].queryset = SimCase.objects.filter(is_active=True).exclude(kind=SimCase.Kind.TERMINAL).order_by("name")
        self.fields["case"].required = False
        self.fields["kind"].choices = PromoCode.Kind.choices  # no empty "---------" option
        if not self.instance.pk:
            self.initial.setdefault("kind", PromoCode.Kind.BALANCE_BONUS)
        for name in ("starts_at", "expires_at"):
            self.fields[name].input_formats = [_DT_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"]
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.Select, forms.DateTimeInput)):
                field.widget.attrs.setdefault("class", "form-control")
        self.fields["code"].widget.attrs.update({"autocapitalize": "characters", "spellcheck": "false", "placeholder": "NARKOZZ"})
        if self.instance.pk and self.instance.owner_id:
            self.initial.setdefault("owner_username", self.instance.owner.username)
        if self.instance.pk and self.instance.activations_count:
            # Users already hold this code: what it is must not change under them.
            for name in ("code", "kind"):
                self.fields[name].disabled = True

    def clean_code(self):
        code = normalize_code(self.cleaned_data.get("code", ""))
        if len(code) < 3:
            raise forms.ValidationError("Код — от 3 символов: латиница, цифры, «-» и «_».")
        duplicate = PromoCode.objects.filter(code=code).exclude(pk=self.instance.pk).exists()
        if duplicate:
            raise forms.ValidationError("Такой код уже есть.")
        return code

    def clean_owner_username(self):
        raw = (self.cleaned_data.get("owner_username") or "").strip()
        if not raw:
            return None
        User = get_user_model()
        user = User.objects.filter(username=raw).first()
        if user is None and raw.isdigit():
            user = User.objects.filter(pk=int(raw)).first()
        if user is None:
            raise forms.ValidationError("Пользователь не найден.")
        return user

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        percent = data.get("percent")
        amount = data.get("amount")
        if kind == PromoCode.Kind.BALANCE_BONUS:
            if not amount or amount <= 0:
                self.add_error("amount", "Укажите сумму бонуса.")
            elif amount > Decimal("100000"):
                self.add_error("amount", "Слишком большая сумма.")
        elif kind == PromoCode.Kind.TOPUP_BONUS:
            if not percent or not 1 <= percent <= 100:
                self.add_error("percent", "Процент от 1 до 100.")
        elif kind == PromoCode.Kind.SUBSCRIPTION_DISCOUNT:
            if not percent or not 1 <= percent <= 90:
                self.add_error("percent", "Скидка от 1 до 90%.")
        elif kind == PromoCode.Kind.FREE_CASE and not data.get("case"):
            self.add_error("case", "Выберите кейс.")
        starts, expires = data.get("starts_at"), data.get("expires_at")
        if starts and expires and expires <= starts:
            self.add_error("expires_at", "Окончание должно быть позже начала.")
        return data

    def save(self, commit=True):
        promo = super().save(commit=False)
        promo.owner = self.cleaned_data.get("owner_username")
        # Fields that do not belong to the chosen kind are cleared, so the code does exactly what it says.
        if promo.kind != PromoCode.Kind.BALANCE_BONUS:
            promo.amount = None
        if promo.kind not in (PromoCode.Kind.TOPUP_BONUS, PromoCode.Kind.SUBSCRIPTION_DISCOUNT):
            promo.percent = None
        if promo.kind != PromoCode.Kind.TOPUP_BONUS:
            promo.min_topup = None
            promo.max_bonus = None
        if promo.kind != PromoCode.Kind.SUBSCRIPTION_DISCOUNT:
            promo.subscription_family = ""
        if promo.kind != PromoCode.Kind.FREE_CASE:
            promo.case = None
        if commit:
            promo.save()
        return promo
