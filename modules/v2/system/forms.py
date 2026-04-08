from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .module_registry import MODULE_REGISTRY
from .models import ProjectVersion, SiteConfiguration, ThemeProfile
from .services import (
    build_effective_module_state_map,
    get_available_themes,
    get_current_project_version,
    get_module_state_map,
    save_site_configuration,
)


class SiteConfigurationForm(forms.ModelForm):
    current_project_version = forms.ModelChoiceField(
        queryset=ProjectVersion.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Current project version",
    )
    active_theme = forms.ModelChoiceField(
        queryset=ThemeProfile.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Active theme",
    )

    class Meta:
        model = SiteConfiguration
        fields = [
            "site_name",
            "site_currency_code",
            "site_currency_rate",
            "support_email",
            "public_base_url",
            "maintenance_mode_enabled",
            "maintenance_message",
            "registration_enabled",
            "current_project_version",
            "active_theme",
        ]
        widgets = {
            "site_name": forms.TextInput(attrs={"class": "form-control"}),
            "site_currency_code": forms.Select(attrs={"class": "form-control"}),
            "site_currency_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001", "min": "0.000001"}),
            "support_email": forms.EmailInput(attrs={"class": "form-control"}),
            "public_base_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://caseup.example.com"}),
            "maintenance_message": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def clean_site_currency_rate(self):
        value = self.cleaned_data["site_currency_rate"]
        if value <= 0:
            raise ValidationError("Курс должен быть больше нуля.")
        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial_module_state_map = get_module_state_map()
        current_version = self.instance.current_project_version or get_current_project_version()
        self.fields["current_project_version"].queryset = ProjectVersion.objects.order_by("version_order")
        self.fields["active_theme"].queryset = ThemeProfile.objects.filter(pk__in=[theme.pk for theme in get_available_themes(project_version=current_version)])
        self.fields["maintenance_mode_enabled"].widget.attrs["class"] = "toggle-control__input"
        self.fields["registration_enabled"].widget.attrs["class"] = "toggle-control__input"
        for key, definition in MODULE_REGISTRY.items():
            self.fields[f"module__{key}"] = forms.BooleanField(
                required=False,
                initial=self.initial_module_state_map.get(key, definition.default_enabled),
                label=definition.label,
                help_text=self._build_help_text(definition),
            )
            self.fields[f"module__{key}"].widget.attrs["class"] = "toggle-control__input"

    def _build_help_text(self, definition):
        parts = [definition.description]
        if definition.dependencies:
            deps = ", ".join(MODULE_REGISTRY[item].label for item in definition.dependencies)
            parts.append(f"Зависит от: {deps}.")
        if definition.addon_targets:
            targets = ", ".join(MODULE_REGISTRY[item].label for item in definition.addon_targets)
            parts.append(f"Коммерчески расширяет: {targets}.")
        return " ".join(parts)

    def clean_public_base_url(self):
        value = (self.cleaned_data.get("public_base_url") or "").strip().rstrip("/")
        if value and not (value.startswith("http://") or value.startswith("https://")):
            raise ValidationError("Public base URL должен начинаться с http:// или https://")
        return value

    def clean(self):
        cleaned_data = super().clean()
        module_states = self.get_module_states(cleaned_data)
        try:
            build_effective_module_state_map(self.initial_module_state_map, module_states)
        except ValidationError as exc:
            self.add_error(None, exc)
        project_version = cleaned_data.get("current_project_version")
        active_theme = cleaned_data.get("active_theme")
        if project_version and active_theme:
            if active_theme.project_version_from and active_theme.project_version_from.version_order > project_version.version_order:
                self.add_error("active_theme", "Тема начинается с более новой версии проекта.")
            if active_theme.project_version_to and active_theme.project_version_to.version_order < project_version.version_order:
                self.add_error("active_theme", "Тема не поддерживает выбранную версию проекта.")
        return cleaned_data

    def get_module_states(self, cleaned_data=None) -> dict[str, bool]:
        cleaned_data = cleaned_data or self.cleaned_data
        return {key: bool(cleaned_data.get(f"module__{key}")) for key in MODULE_REGISTRY}

    def get_submitted_module_states(self) -> dict[str, bool]:
        if not self.is_bound:
            return self.initial_module_state_map.copy()
        submitted: dict[str, bool] = {}
        for key in MODULE_REGISTRY:
            field_name = f"module__{key}"
            field = self.fields[field_name]
            submitted[key] = bool(field.widget.value_from_datadict(self.data, self.files, self.add_prefix(field_name)))
        return submitted

    def save(self, commit=True):
        instance: SiteConfiguration = super().save(commit=False)
        module_states = self.get_module_states()
        if not commit:
            return instance
        return save_site_configuration(config=instance, module_states=module_states)



class ThemeActivateForm(forms.Form):
    theme = forms.ModelChoiceField(
        queryset=ThemeProfile.objects.none(),
        required=True,
        widget=forms.RadioSelect,
        empty_label=None,
    )
    next_url = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        current_version = kwargs.pop("current_version", None)
        super().__init__(*args, **kwargs)
        self.fields["theme"].queryset = ThemeProfile.objects.filter(
            pk__in=[theme.pk for theme in get_available_themes(project_version=current_version or get_current_project_version())]
        ).order_by("-is_default", "name")
