from django import forms

from .models import UserPrivacySettings


class PrivacySettingsForm(forms.ModelForm):
    class Meta:
        model = UserPrivacySettings
        fields = [
            "who_can_send_friend_request",
            "who_can_send_direct",
            "who_can_see_online",
            "who_can_see_profile",
        ]
        widgets = {
            "who_can_send_friend_request": forms.Select(attrs={"class": "form-select"}),
            "who_can_send_direct": forms.Select(attrs={"class": "form-select"}),
            "who_can_see_online": forms.Select(attrs={"class": "form-select"}),
            "who_can_see_profile": forms.Select(attrs={"class": "form-select"}),
        }
