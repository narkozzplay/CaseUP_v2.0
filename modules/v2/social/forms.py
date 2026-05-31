from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from .models import SocialSpace


User = get_user_model()


class BaseSocialSpaceCreateForm(forms.Form):
    privacy_choices = SocialSpace.Privacy.choices

    title = forms.CharField(max_length=160)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    privacy = forms.ChoiceField(choices=privacy_choices)
    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.SelectMultiple,
    )

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = current_user
        member_qs = User.objects.filter(is_active=True).order_by("username", "id")
        if getattr(current_user, "pk", None):
            member_qs = member_qs.exclude(pk=current_user.pk)
        self.fields["members"].queryset = member_qs
        self.fields["members"].label_from_instance = lambda user: user.username

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class SocialGroupCreateForm(BaseSocialSpaceCreateForm):
    privacy_choices = SocialSpace.Privacy.choices


class SocialConversationCreateForm(BaseSocialSpaceCreateForm):
    privacy_choices = SocialSpace.Privacy.choices

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["privacy"].initial = SocialSpace.Privacy.PRIVATE
