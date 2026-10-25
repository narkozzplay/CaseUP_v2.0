from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms

from .models import DonationGoal

MAX_GOAL = Decimal("100000000.00")


class DonationGoalForm(forms.ModelForm):
    """Control Center form: the goal texts, the money, the look and where the widget shows."""

    class Meta:
        model = DonationGoal
        fields = (
            "is_enabled",
            "title",
            "description",
            "goal_amount",
            "collected_offset",
            "deadline",
            "preset_amounts",
            "min_amount",
            "max_amount",
            "button_text",
            "thank_you_text",
            "allow_messages",
            "variant",
            "background",
            "accent_color",
            "accent_color_end",
            "icon",
            "show_percent",
            "show_goal",
            "show_donors_count",
            "show_recent_donors",
            "show_on_home",
            "show_on_dashboard",
            "show_in_sidebar",
            "show_in_menu",
            "sponsors_enabled",
            "sponsor_top_size",
            "sponsor_monthly_min",
            "sponsor_lifetime_min",
            "sponsor_notify_donor",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "deadline": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color_end": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect)):
                field.widget.attrs.setdefault("class", "form-control")
            field.widget.attrs["data-donation-field"] = name
        self.fields["collected_offset"].required = False

    def clean_goal_amount(self):
        value = self.cleaned_data["goal_amount"]
        if value is None or value <= 0 or value > MAX_GOAL:
            raise forms.ValidationError("Цель — сумма больше нуля.")
        return value

    def clean_collected_offset(self):
        value = self.cleaned_data["collected_offset"]
        if value is None:
            return Decimal("0.00")
        if value < 0 or value > MAX_GOAL:
            raise forms.ValidationError("Сумма вне сайта не может быть отрицательной.")
        return value

    def clean_min_amount(self):
        value = self.cleaned_data["min_amount"]
        if value is None or value < 1:
            raise forms.ValidationError("Минимальный донат — от 1 ₽.")
        return value

    def clean_sponsor_top_size(self):
        value = self.cleaned_data["sponsor_top_size"]
        if value is None or not 1 <= value <= 20:
            raise forms.ValidationError("Топ — от 1 до 20 донатеров.")
        return value

    def clean_sponsor_monthly_min(self):
        value = self.cleaned_data["sponsor_monthly_min"]
        if value is None or value < 0:
            raise forms.ValidationError("Порог не может быть отрицательным.")
        return value

    def clean_sponsor_lifetime_min(self):
        value = self.cleaned_data["sponsor_lifetime_min"]
        if value is None or value < 0:
            raise forms.ValidationError("Порог не может быть отрицательным.")
        return value

    def clean_preset_amounts(self):
        raw = self.cleaned_data["preset_amounts"] or ""
        values = []
        for chunk in raw.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                amount = Decimal(chunk.replace(" ", ""))
            except InvalidOperation as exc:
                raise forms.ValidationError(f"«{chunk}» — не число.") from exc
            if amount <= 0:
                raise forms.ValidationError("Быстрые суммы должны быть больше нуля.")
            values.append(amount)
        if len(values) > 6:
            raise forms.ValidationError("Не больше шести быстрых сумм.")
        return ",".join(f"{value.normalize():f}" for value in values)

    def clean(self):
        cleaned = super().clean()
        minimum, maximum = cleaned.get("min_amount"), cleaned.get("max_amount")
        if minimum is not None and maximum is not None and maximum < minimum:
            self.add_error("max_amount", "Максимум меньше минимума.")
        presets = [Decimal(chunk) for chunk in (cleaned.get("preset_amounts") or "").split(",") if chunk]
        if minimum is not None and maximum is not None and any(not minimum <= value <= maximum for value in presets):
            self.add_error("preset_amounts", "Быстрые суммы должны быть между минимумом и максимумом.")
        return cleaned


class DonateForm(forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, required=False)
    preset = forms.DecimalField(max_digits=14, decimal_places=2, required=False)
    message = forms.CharField(max_length=200, required=False)
    is_anonymous = forms.BooleanField(required=False)
    token = forms.UUIDField()

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("preset") or cleaned.get("amount")
        if amount is None:
            raise forms.ValidationError("Укажите сумму доната.")
        cleaned["amount"] = amount
        return cleaned
