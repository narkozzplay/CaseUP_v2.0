from __future__ import annotations

from django import forms

from .models import SteamItem, StockMovement, StockReason, UserPriceAlertRule


class AddSteamItemForm(forms.Form):
    value = forms.CharField(
        label="market_hash_name или Steam listing URL",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "AK-47 | Redline (Field-Tested) или https://steamcommunity.com/market/listings/730/...",
            }
        ),
    )
    stock = forms.IntegerField(
        label="Начальный stock",
        min_value=0,
        initial=0,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )


class SteamItemNameRuForm(forms.ModelForm):
    class Meta:
        model = SteamItem
        fields = ["name_ru"]
        widgets = {
            "name_ru": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "РУ_нейм заполняется вручную после добавления",
                }
            )
        }


class StockMovementCreateForm(forms.Form):
    reason = forms.ModelChoiceField(
        label="Причина",
        queryset=StockReason.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    qty = forms.IntegerField(
        label="Количество",
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    note = forms.CharField(
        label="Комментарий",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].queryset = StockReason.objects.filter(is_active=True)

    def save(self, *, item: SteamItem, created_by=None) -> StockMovement:
        reason: StockReason = self.cleaned_data["reason"]
        qty: int = int(self.cleaned_data["qty"])
        note: str = (self.cleaned_data.get("note") or "").strip()

        delta = qty if reason.direction == StockReason.Direction.IN else -qty

        move = StockMovement(
            item=item,
            reason=reason,
            delta=delta,
            note=note,
            created_by=created_by,
        )
        move.full_clean()
        move.save()
        return move


def lock_alert_quota(user) -> None:
    """Lock the user's row so concurrent alert saves count active alerts one at a time (call inside atomic)."""
    from django.contrib.auth import get_user_model

    get_user_model().objects.select_for_update().filter(pk=user.pk).exists()


def alert_limit_error(user) -> str:
    """Error text when the user already has as many active alerts as their plan allows, else ""."""
    from modules.v2.subscriptions.limits import PRICE_ALERTS
    from modules.v2.subscriptions.services import get_user_limit

    limit = get_user_limit(user, PRICE_ALERTS)
    if limit is None:
        return ""
    active = UserPriceAlertRule.objects.filter(user=user, status=UserPriceAlertRule.Status.ACTIVE).count()
    if active < limit:
        return ""
    return f"Активных алертов уже {active} из {limit} по вашему тарифу. Выключите один из них или повысьте тариф."


class UserPriceAlertRuleForm(forms.ModelForm):
    class Meta:
        model = UserPriceAlertRule
        fields = [
            "name", "scope_type", "item", "family_name", "item_type", "direction", "reference_point",
            "threshold_mode", "window_kind", "window_value", "min_change_rub", "min_change_percent",
            "cooldown_minutes", "repeat_step_rub", "repeat_step_percent", "repeat_mode",
            "transport", "delivery_target", "status",
        ]
        widgets = {field: forms.TextInput(attrs={"class": "form-control"}) for field in ["name", "family_name"]}
        widgets.update({
            "scope_type": forms.Select(attrs={"class": "form-control"}),
            "item": forms.Select(attrs={"class": "form-control"}),
            "item_type": forms.Select(attrs={"class": "form-control"}),
            "direction": forms.Select(attrs={"class": "form-control"}),
            "reference_point": forms.Select(attrs={"class": "form-control"}),
            "threshold_mode": forms.Select(attrs={"class": "form-control"}),
            "window_kind": forms.Select(attrs={"class": "form-control"}),
            "window_value": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "min_change_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "min_change_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "cooldown_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "repeat_step_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "repeat_step_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "repeat_mode": forms.Select(attrs={"class": "form-control"}),
            "transport": forms.Select(attrs={"class": "form-control"}),
            "delivery_target": forms.Select(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
        })
        labels = {
            "name": "Название правила",
            "scope_type": "Что отслеживаем",
            "item": "Предмет",
            "family_name": "Семейство",
            "item_type": "Тип предметов",
            "direction": "Направление",
            "reference_point": "От чего считать",
            "threshold_mode": "Как сравнивать порог",
            "window_kind": "Окно анализа",
            "window_value": "Размер окна",
            "min_change_rub": "Порог, ₽",
            "min_change_percent": "Порог, %",
            "cooldown_minutes": "Пауза между уведомлениями, мин",
            "repeat_step_rub": "Шаг повтора, ₽",
            "repeat_step_percent": "Шаг повтора, %",
            "repeat_mode": "Повтор",
            "transport": "Канал",
            "delivery_target": "Куда отправлять",
            "status": "Статус",
        }
        help_texts = {
            "family_name": "Например, AK-47 | Redline. Сработает на любой износ.",
            "window_value": "Число часов, дней или точек истории.",
            "repeat_step_rub": "Для режима «Повторно по шагам».",
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = SteamItem.objects.filter(is_active=True).order_by("family_name", "market_hash_name")
        self.fields["item"].required = False
        self.fields["family_name"].required = False
        self.fields["item_type"].required = False
        if user is not None:
            self.instance.user = user
        self._was_active = bool(self.instance.pk) and self.instance.status == UserPriceAlertRule.Status.ACTIVE

    def clean(self):
        cleaned_data = super().clean()
        user = getattr(self.instance, "user", None)
        becomes_active = cleaned_data.get("status") == UserPriceAlertRule.Status.ACTIVE and not self._was_active
        if user is not None and becomes_active:
            message = alert_limit_error(user)
            if message:
                self.add_error("status", message)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.full_clean()
        if commit:
            instance.save()
        return instance
