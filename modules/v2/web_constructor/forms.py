from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import PageTemplate, TemplateAsset
from .services.schema import build_default_schema, get_editor_manifest


class PageTemplateCreateForm(forms.ModelForm):
    class Meta:
        model = PageTemplate
        fields = [
            "title",
            "slug",
            "description",
            "page_type",
            "visibility",
            "access_mode",
            "is_free",
            "price",
            "currency",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Например: Neon Landing"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "placeholder": "neon-landing"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "page_type": forms.Select(attrs={"class": "form-control"}),
            "visibility": forms.Select(attrs={"class": "form-control"}),
            "access_mode": forms.Select(attrs={"class": "form-control"}),
            "is_free": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "currency": forms.Select(attrs={"class": "form-control"}),
        }

    def clean_slug(self):
        return slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("title") or "")[:160]

    def clean(self):
        cleaned = super().clean()
        is_free = cleaned.get("is_free")
        price = cleaned.get("price")
        if not is_free and (price is None or price <= 0):
            self.add_error("price", "Для платного шаблона цена должна быть больше нуля.")
        return cleaned


class PageTemplateEditorForm(forms.ModelForm):
    schema_json = forms.CharField(widget=forms.HiddenInput)
    changelog = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Что изменилось в этой версии"}),
    )

    class Meta:
        model = PageTemplate
        fields = [
            "title",
            "slug",
            "description",
            "page_type",
            "visibility",
            "access_mode",
            "is_free",
            "price",
            "currency",
            "cover_image",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "page_type": forms.Select(attrs={"class": "form-control"}),
            "visibility": forms.Select(attrs={"class": "form-control"}),
            "access_mode": forms.Select(attrs={"class": "form-control"}),
            "is_free": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "currency": forms.Select(attrs={"class": "form-control"}),
            "cover_image": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        initial_schema = kwargs.pop("initial_schema", None)
        super().__init__(*args, **kwargs)
        self.initial.setdefault("schema_json", json.dumps(initial_schema or build_default_schema(self.instance.title if self.instance.pk else "")))
        self.editor_manifest = get_editor_manifest()

    def clean_slug(self):
        return slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("title") or "")[:160]

    def clean_schema_json(self):
        raw = self.cleaned_data.get("schema_json") or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError("Schema JSON повреждён.") from exc
        if not isinstance(parsed, dict):
            raise ValidationError("Schema JSON должен быть объектом.")
        return parsed

    def clean(self):
        cleaned = super().clean()
        is_free = cleaned.get("is_free")
        price = cleaned.get("price")
        if not is_free and (price is None or price <= 0):
            self.add_error("price", "Для платного шаблона цена должна быть больше нуля.")
        return cleaned


class TemplateAssetUploadForm(forms.ModelForm):
    class Meta:
        model = TemplateAsset
        fields = ["title", "alt_text", "asset_type", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Hero background"}),
            "alt_text": forms.TextInput(attrs={"class": "form-control", "placeholder": "Описание картинки"}),
            "asset_type": forms.Select(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }


class TemplateModerationForm(forms.Form):
    moderation_comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Комментарий модератора"}),
    )
