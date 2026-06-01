from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from modules.v2.profiles.forms import validate_uploaded_avatar

from .models import SocialMembership, SocialSpace
from .services import VISIBLE_MEMBERSHIP_STATUSES, get_active_user_membership


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
        self.fields["privacy"].choices = self.privacy_choices

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class SocialGroupCreateForm(BaseSocialSpaceCreateForm):
    privacy_choices = SocialSpace.Privacy.choices


class SocialConversationCreateForm(BaseSocialSpaceCreateForm):
    privacy_choices = ((SocialSpace.Privacy.PRIVATE, SocialSpace.Privacy.PRIVATE.label),)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["privacy"].initial = SocialSpace.Privacy.PRIVATE


class SocialMessageCreateForm(forms.Form):
    body = forms.CharField(
        max_length=4000,
        error_messages={
            "required": "Message cannot be empty.",
            "max_length": "Message must be 4000 characters or fewer.",
        },
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def clean_body(self):
        body = str(self.cleaned_data.get("body") or "").strip()
        if not body:
            raise forms.ValidationError("Message cannot be empty.")
        return body


class SocialSpaceSettingsForm(forms.ModelForm):
    class Meta:
        model = SocialSpace
        fields = ["title", "description", "privacy", "avatar"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "avatar": forms.FileInput(attrs={"accept": "image/*"}),
        }

    def __init__(self, *args, current_user=None, space=None, **kwargs):
        self.current_user = current_user
        self.space = space or kwargs.get("instance")
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["avatar"].widget.attrs.setdefault("accept", "image/*")

        membership = get_active_user_membership(current_user, self.space) if current_user is not None and self.space is not None else None
        is_owner = bool(membership and membership.role == SocialMembership.Role.OWNER)
        if not is_owner:
            self.fields["title"].disabled = True
            self.fields["privacy"].disabled = True
            self.fields["title"].help_text = "Only the owner can change the title."
            self.fields["privacy"].help_text = "Only the owner can change privacy."

    def clean_title(self):
        title = str(self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Social space title is required.")
        return title

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if self.files.get(self.add_prefix("avatar")):
            validate_uploaded_avatar(avatar)
        return avatar


class SpaceMemberAddForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.none())

    def __init__(self, *args, current_user=None, space=None, **kwargs):
        super().__init__(*args, **kwargs)
        member_ids = []
        if space is not None:
            member_ids = list(
                SocialMembership.objects.filter(
                    space=space,
                    status__in=VISIBLE_MEMBERSHIP_STATUSES,
                ).values_list("user_id", flat=True)
            )
        queryset = User.objects.filter(is_active=True).order_by("username", "id")
        if getattr(current_user, "pk", None):
            queryset = queryset.exclude(pk=current_user.pk)
        if member_ids:
            queryset = queryset.exclude(pk__in=member_ids)
        self.fields["user"].queryset = queryset
        self.fields["user"].label_from_instance = lambda user: user.username
        self.fields["user"].widget.attrs.setdefault("class", "form-control")


class SpaceMemberRoleForm(forms.Form):
    role = forms.ChoiceField(
        choices=(
            (SocialMembership.Role.ADMIN, SocialMembership.Role.ADMIN.label),
            (SocialMembership.Role.MEMBER, SocialMembership.Role.MEMBER.label),
        ),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
