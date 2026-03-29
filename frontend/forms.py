from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class DevBalanceTopUpForm(forms.Form):
    username = forms.CharField(max_length=150, label="Username")
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise forms.ValidationError("User not found.") from exc

        self.cleaned_data["target_user"] = user
        return username