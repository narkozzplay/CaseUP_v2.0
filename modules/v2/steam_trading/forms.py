from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem

from .models import SteamPurchase, SteamPurchaseSale


CASE_ITEM_QUERYSET = SteamItem.objects.filter(
    is_active=True,
    item_type=SteamItem.ItemType.CASE,
).order_by("family_name", "market_hash_name")


class SteamPurchaseForm(forms.ModelForm):
    class Meta:
        model = SteamPurchase
        fields = ["item", "purchased_at", "quantity", "purchase_price_rub"]
        widgets = {
            "item": forms.Select(attrs={"class": "form-control"}),
            "purchased_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "purchase_price_rub": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": "0.01"}),
        }
        labels = {
            "item": "Кейс",
            "purchased_at": "Дата покупки",
            "quantity": "Количество",
            "purchase_price_rub": "Цена за штуку, ₽",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = CASE_ITEM_QUERYSET
        self.fields["purchased_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["quantity"].initial = self.initial.get("quantity") or 1
        self.fields["purchased_at"].initial = self.initial.get("purchased_at") or timezone.localtime().replace(second=0, microsecond=0)

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise ValidationError("Количество должно быть больше нуля.")
        return quantity

    def clean_purchase_price_rub(self):
        price = self.cleaned_data["purchase_price_rub"]
        if price < 0:
            raise ValidationError("Цена покупки не может быть отрицательной.")
        return price

    def clean_item(self):
        item = self.cleaned_data["item"]
        if item.item_type != SteamItem.ItemType.CASE:
            raise ValidationError("Для MVP доступны только case-предметы.")
        if not item.is_active:
            raise ValidationError("Можно выбирать только активные предметы каталога.")
        return item

    def save(self, *, created_by=None, commit=True):
        instance: SteamPurchase = super().save(commit=False)
        if created_by is not None and not instance.created_by_id:
            instance.created_by = created_by
        if commit:
            instance.save()
        return instance


class SteamPurchaseSaleForm(forms.ModelForm):
    class Meta:
        model = SteamPurchaseSale
        fields = ["sold_at", "quantity", "sale_price_rub"]
        widgets = {
            "sold_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "sale_price_rub": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": "0.01"}),
        }
        labels = {
            "sold_at": "Дата продажи",
            "quantity": "Количество",
            "sale_price_rub": "Цена продажи за штуку, ₽",
        }

    def __init__(self, *args, purchase: SteamPurchase, **kwargs):
        self.purchase = purchase
        super().__init__(*args, **kwargs)
        self.instance.purchase = purchase
        self.fields["sold_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["sold_at"].initial = self.initial.get("sold_at") or timezone.localtime().replace(second=0, microsecond=0)
        self.fields["quantity"].initial = self.initial.get("quantity") or (1 if purchase.remaining_quantity > 0 else 0)
        self.fields["sale_price_rub"].initial = self.initial.get("sale_price_rub") or purchase.current_price_rub or Decimal("0.00")

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise ValidationError("Количество продажи должно быть больше нуля.")
        if quantity > self.purchase.remaining_quantity:
            raise ValidationError(f"Нельзя продать больше остатка. Доступно: {self.purchase.remaining_quantity}.")
        return quantity

    def clean_sale_price_rub(self):
        sale_price_rub = self.cleaned_data["sale_price_rub"]
        if sale_price_rub < 0:
            raise ValidationError("Цена продажи не может быть отрицательной.")
        return sale_price_rub

    def clean(self):
        cleaned_data = super().clean()
        sold_at = cleaned_data.get("sold_at")
        if not sold_at:
            self.add_error("sold_at", "Укажите дату и время продажи.")
        return cleaned_data

    def save(self, *, created_by=None, commit=True):
        instance: SteamPurchaseSale = super().save(commit=False)
        instance.purchase = self.purchase
        if created_by is not None and not instance.created_by_id:
            instance.created_by = created_by
        if commit:
            instance.save()
        return instance
