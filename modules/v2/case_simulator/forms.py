from __future__ import annotations

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import SimCase, SimCaseItem, TerminalSession


def _text(**attrs):
    return forms.TextInput(attrs={"class": "form-control", **attrs})


def _number(**attrs):
    return forms.NumberInput(attrs={"class": "form-control", **attrs})


class SimCaseForm(forms.ModelForm):
    class Meta:
        model = SimCase
        fields = (
            "name", "slug", "kind", "collection_name", "description",
            "case_price", "key_price", "image", "image_url", "accent_color",
            "terminal_offers", "terminal_markup_percent", "is_active", "sort_order",
        )
        labels = {
            "name": "Название",
            "slug": "Адрес (slug)",
            "kind": "Тип",
            "collection_name": "Коллекция",
            "description": "Описание",
            "case_price": "Цена кейса, ₽",
            "key_price": "Цена ключа, ₽",
            "image": "Обложка (файл)",
            "image_url": "Обложка (ссылка)",
            "accent_color": "Цвет акцента",
            "terminal_offers": "Предложений терминала",
            "terminal_markup_percent": "Наценка терминала, %",
            "is_active": "Показывать в симуляторе",
            "sort_order": "Порядок",
        }
        widgets = {
            "name": _text(),
            "slug": _text(placeholder="dreams-and-nightmares"),
            "kind": forms.Select(attrs={"class": "form-control"}),
            "collection_name": _text(),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "case_price": _number(min=0, step="0.01"),
            "key_price": _number(min=0, step="0.01"),
            "image_url": _text(placeholder="https://"),
            "accent_color": forms.TextInput(attrs={"class": "form-control", "type": "color"}),
            "terminal_offers": _number(min=1, max=20),
            "terminal_markup_percent": _number(min=0, max=500),
            "sort_order": _number(min=0),
        }
        help_texts = {
            "slug": "Латиница, цифры и дефис. Используется в адресе /case-simulator/<slug>/.",
            "image": "Файл важнее ссылки. Без обеих берётся обложка из static/img/cases/<slug>.webp.",
        }

    def clean_kind(self):
        kind = self.cleaned_data.get("kind")
        case = self.instance
        if (
            case.pk
            and case.kind == SimCase.Kind.TERMINAL
            and kind != SimCase.Kind.TERMINAL
            and case.terminal_sessions.filter(status=TerminalSession.Status.ACTIVE).exists()
        ):
            raise forms.ValidationError("У игроков есть незавершённые сессии терминала: тип можно сменить, когда они закончатся.")
        return kind

    def _non_negative(self, name):
        value = self.cleaned_data.get(name)
        if value is not None and value < 0:
            raise forms.ValidationError("Цена не может быть отрицательной.")
        return value

    def clean_case_price(self):
        return self._non_negative("case_price")

    def clean_key_price(self):
        return self._non_negative("key_price")

    def clean_terminal_markup_percent(self):
        value = self.cleaned_data.get("terminal_markup_percent") or 0
        if value > 500:
            raise forms.ValidationError("Не больше 500%.")
        return value

    def clean_terminal_offers(self):
        value = self.cleaned_data.get("terminal_offers") or 0
        if not 1 <= value <= 20:
            raise forms.ValidationError("От 1 до 20.")
        return value


class SimCaseItemForm(forms.ModelForm):
    class Meta:
        model = SimCaseItem
        fields = ("name", "weapon", "rarity", "value", "weight", "min_float", "max_float", "stattrak_allowed", "image_url", "sort_order")
        widgets = {
            "name": _text(placeholder="AK-47 | Nightwish"),
            "weapon": _text(),
            "rarity": forms.Select(attrs={"class": "form-control"}),
            "value": _number(min=0, step="0.01"),
            "weight": _number(min=0, step="0.0001", placeholder="авто"),
            "min_float": _number(min=0, max=1, step="0.0001"),
            "max_float": _number(min=0, max=1, step="0.0001"),
            "image_url": _text(placeholder="https://"),
            "sort_order": _number(min=0),
        }

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("min_float"), cleaned.get("max_float")
        if (low is None) != (high is None):
            raise forms.ValidationError("Float: заполните оба поля или оставьте оба пустыми.")
        if low is not None and high is not None:
            if not (0 <= low <= high <= 1):
                raise forms.ValidationError("Float: 0 ≤ мин ≤ макс ≤ 1.")
        weight = cleaned.get("weight")
        if weight is not None and weight < 0:
            self.add_error("weight", "Вес не может быть отрицательным.")
        value = cleaned.get("value")
        if value is not None and value < 0:
            self.add_error("value", "Стоимость не может быть отрицательной.")
        return cleaned


class BaseSimCaseItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        alive = 0
        offered_ids = None
        for form in self.forms:
            if not getattr(form, "cleaned_data", None):
                continue
            if self.can_delete and self._should_delete_form(form):
                item = form.instance
                if item.pk and item.openings.exists():
                    raise forms.ValidationError(
                        f"«{item.name}» уже выпадал игрокам, его нельзя удалить. Поставьте вес 0, чтобы он больше не выпадал."
                    )
                if item.pk:
                    if offered_ids is None:
                        offered_ids = self._active_offer_item_ids()
                    if item.pk in offered_ids:
                        raise forms.ValidationError(
                            f"«{item.name}» сейчас в предложениях открытого терминала, удалить его нельзя. Поставьте вес 0."
                        )
                continue
            alive += 1
        if alive == 0 and self.instance.is_active:
            raise forms.ValidationError("В активном кейсе должен быть хотя бы один предмет.")


    def _active_offer_item_ids(self) -> set[int]:
        sessions = TerminalSession.objects.filter(case=self.instance, status=TerminalSession.Status.ACTIVE)
        return {offer.get("item_id") for offers in sessions.values_list("offers", flat=True) for offer in offers or []}


SimCaseItemFormSet = inlineformset_factory(
    SimCase,
    SimCaseItem,
    form=SimCaseItemForm,
    formset=BaseSimCaseItemFormSet,
    extra=3,
    can_delete=True,
)
