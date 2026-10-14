from __future__ import annotations

from decimal import Decimal

from django import forms

from .services import get_topup_limits


class TopupForm(forms.Form):
    # Not required at the field level: the preset buttons submit the same form without the custom amount
    # (the view copies the preset into "amount"), and a browser "required" would block them.
    amount = forms.DecimalField(max_digits=14, decimal_places=2, label="Сумма", required=False)
    accept_terms = forms.BooleanField(
        label="Я принимаю условия Пользовательского соглашения",
        error_messages={"required": "Чтобы пополнить баланс, примите условия Пользовательского соглашения."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum, self.maximum = get_topup_limits()
        self.fields["amount"].widget.attrs.update(
            {
                "class": "form-control topup-amount__input",
                "min": f"{self.minimum:.0f}",
                "max": f"{self.maximum:.0f}",
                "step": "1",
                "inputmode": "decimal",
                "placeholder": f"{self.minimum:.0f}",
            }
        )

    def clean_amount(self):
        amount: Decimal | None = self.cleaned_data["amount"]
        if amount is None:
            raise forms.ValidationError("Укажите сумму пополнения.")
        if amount < self.minimum or amount > self.maximum:
            raise forms.ValidationError(f"Сумма должна быть от {self.minimum:.0f} до {self.maximum:.0f}.")
        return amount
