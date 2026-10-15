from __future__ import annotations

from django import forms

from .models import CATEGORY_LABELS_RU, PriceAutoUpdateRule

SCHEDULE_MODE_LABELS = (
    (PriceAutoUpdateRule.ScheduleMode.HOURLY, "Раз в час, в выбранную минуту"),
    (PriceAutoUpdateRule.ScheduleMode.INTERVAL, "Каждые N минут"),
)
INTERVAL_LABELS = tuple((minutes, f"каждые {minutes} мин") for minutes, _label in PriceAutoUpdateRule.INTERVAL_CHOICES)


class PriceAutoUpdateRuleForm(forms.ModelForm):
    class Meta:
        model = PriceAutoUpdateRule
        fields = [
            "category",
            "schedule_mode",
            "interval_minutes",
            "start_at",
            "hourly_minute",
            "is_enabled",
        ]
        widgets = {
            "start_at": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "form-control",
                },
                format="%Y-%m-%dT%H:%M",
            ),
            "hourly_minute": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 0,
                    "max": 59,
                    "placeholder": "Например: 15",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        data = kwargs.get("data")
        if data is not None and "schedule_mode" not in data:
            mutable_data = data.copy()
            mutable_data["schedule_mode"] = PriceAutoUpdateRule.ScheduleMode.INTERVAL
            kwargs["data"] = mutable_data

        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            if name in {"is_enabled", "start_at", "hourly_minute"}:
                continue
            field.widget.attrs.setdefault("class", "form-control")

        # Russian labels and only the categories that do not have a rule yet (one rule per category).
        taken = set(PriceAutoUpdateRule.objects.exclude(pk=self.instance.pk).values_list("category", flat=True))
        self.fields["category"].choices = [("", "Выберите категорию")] + [
            (code, CATEGORY_LABELS_RU.get(code, label))
            for code, label in self.fields["category"].choices
            if code and code not in taken
        ]
        self.fields["schedule_mode"].choices = SCHEDULE_MODE_LABELS
        self.fields["interval_minutes"].choices = INTERVAL_LABELS
        self.fields["is_enabled"].widget.attrs.pop("class", None)
        self.fields["start_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["schedule_mode"].required = False

    def clean(self):
        cleaned_data = super().clean()

        schedule_mode = cleaned_data.get("schedule_mode") or PriceAutoUpdateRule.ScheduleMode.INTERVAL
        cleaned_data["schedule_mode"] = schedule_mode

        if schedule_mode == PriceAutoUpdateRule.ScheduleMode.HOURLY:
            cleaned_data["start_at"] = None

            minute = cleaned_data.get("hourly_minute")
            if minute is None:
                self.add_error("hourly_minute", "Укажи минуту запуска от 0 до 59.")
            elif not 0 <= int(minute) <= 59:
                self.add_error("hourly_minute", "Минута запуска должна быть в диапазоне 0..59.")
        else:
            cleaned_data["hourly_minute"] = None

        return cleaned_data