from __future__ import annotations

from django import forms

from .models import TradeBotAccount


class TradeBotAccountForm(forms.ModelForm):
    class Meta:
        model = TradeBotAccount
        fields = [
            "title",
            "steam_id64",
            "trade_url",
            "login_hint",
            "status",
            "sort_order",
            "is_enabled",
            "is_default",
            "notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "steam_id64": forms.TextInput(attrs={"class": "form-control"}),
            "trade_url": forms.TextInput(attrs={"class": "form-control"}),
            "login_hint": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }
