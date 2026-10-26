from __future__ import annotations

from django import forms

from .models import AdPlacement, AdSettings, HouseBanner

_DT_FORMAT = "%Y-%m-%dT%H:%M"


def _dt_widget():
    return forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}, format=_DT_FORMAT)


class AdSettingsForm(forms.ModelForm):
    class Meta:
        model = AdSettings
        fields = ("is_enabled", "provider", "adsense_client_id", "feed_every_n", "show_remove_link", "show_to_staff")
        widgets = {
            "provider": forms.Select(attrs={"class": "form-control"}),
            "adsense_client_id": forms.TextInput(attrs={"class": "form-control"}),
            "feed_every_n": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100}),
        }


class HouseBannerForm(forms.ModelForm):
    class Meta:
        model = HouseBanner
        fields = (
            "name", "title", "text", "image", "video", "url", "button",
            "placements", "audience", "show_to_ad_free", "weight", "starts_at", "ends_at", "is_enabled",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "text": forms.TextInput(attrs={"class": "form-control"}),
            "url": forms.TextInput(attrs={"class": "form-control", "placeholder": "/donate/"}),
            "button": forms.TextInput(attrs={"class": "form-control"}),
            "placements": forms.CheckboxSelectMultiple,
            "audience": forms.Select(attrs={"class": "form-control"}),
            "weight": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 100}),
            "starts_at": _dt_widget(),
            "ends_at": _dt_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["placements"].queryset = AdPlacement.objects.order_by("sort_order", "id")
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = [_DT_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"]
