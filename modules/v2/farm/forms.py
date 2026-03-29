from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import formset_factory
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem

from .models import (
    ActiveDropPoolEntry,
    FarmAccount,
    FarmDropSale,
    FarmDropSession,
    FarmModuleConfig,
    FarmerRewardTier,
    WeeklyDropLog,
)
from .services import (
    create_weekly_drop,
    get_drop_week_choices,
    get_farm_module_config,
    parse_trade_url,
    resolve_sale_fee_percent,
)


DROP_ITEM_TYPE_CHOICES = (
    ("", "Все типы"),
    (SteamItem.ItemType.CASE, "Case"),
    (SteamItem.ItemType.SKIN, "Skin"),
    (SteamItem.ItemType.GRAFFITI, "Graffiti"),
)

DROP_SCOPE_CHOICES = (
    ("active", "Активный пул"),
    ("all", "Все предметы"),
)

ALLOWED_DROP_ITEM_TYPES = (
    SteamItem.ItemType.CASE,
    SteamItem.ItemType.SKIN,
    SteamItem.ItemType.GRAFFITI,
)


def build_drop_item_queryset(
    *,
    scope: str = "active",
    item_type: str = "",
    search: str = "",
):
    qs = SteamItem.objects.filter(
        is_active=True,
        item_type__in=ALLOWED_DROP_ITEM_TYPES,
    )

    if scope == "active":
        qs = qs.filter(active_drop_entries__is_active=True)

    if item_type in ALLOWED_DROP_ITEM_TYPES:
        qs = qs.filter(item_type=item_type)

    if search:
        qs = qs.filter(
            Q(market_hash_name__icontains=search)
            | Q(name__icontains=search)
            | Q(name_ru__icontains=search)
            | Q(family_name__icontains=search)
        )

    return qs.distinct().order_by("item_type", "family_name", "market_hash_name")


class FarmAccountForm(forms.ModelForm):
    class Meta:
        model = FarmAccount
        fields = [
            "title",
            "steam_id64",
            "trade_url",
            "login_hint",
            "status",
            "notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "steam_id64": forms.TextInput(attrs={"class": "form-control"}),
            "trade_url": forms.TextInput(attrs={"class": "form-control"}),
            "login_hint": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()

        steam_id64 = (cleaned_data.get("steam_id64") or "").strip()
        trade_url = (cleaned_data.get("trade_url") or "").strip()

        if trade_url:
            try:
                parsed = parse_trade_url(trade_url)
            except ValueError as exc:
                self.add_error("trade_url", str(exc))
                return cleaned_data

            if steam_id64 and steam_id64 != parsed.steam_id64:
                self.add_error(
                    "steam_id64",
                    "steam_id64 не совпадает с partner из trade URL.",
                )
            else:
                cleaned_data["steam_id64"] = parsed.steam_id64

        return cleaned_data


class WeeklyDropForm(forms.Form):
    account = forms.ModelChoiceField(
        label="Аккаунт",
        queryset=FarmAccount.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    week_index = forms.ChoiceField(
        label="Неделя",
        choices=(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    item_1 = forms.ModelChoiceField(
        label="Предмет 1",
        queryset=SteamItem.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    item_2 = forms.ModelChoiceField(
        label="Предмет 2",
        queryset=SteamItem.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    notes = forms.CharField(
        label="Комментарий",
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Например: weekly drop после матчей premier",
            }
        ),
    )

    def __init__(self, *args, user, access, item_queryset, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.access = access

        account_qs = FarmAccount.objects.select_related("owner")
        if not access.staff_access:
            account_qs = account_qs.filter(owner=user)

        self.fields["account"].queryset = account_qs.order_by("sort_order", "title")
        self.fields["week_index"].choices = get_drop_week_choices()
        self.fields["item_1"].queryset = item_queryset
        self.fields["item_2"].queryset = item_queryset

    def clean(self):
        cleaned_data = super().clean()

        account = cleaned_data.get("account")
        item_1 = cleaned_data.get("item_1")
        item_2 = cleaned_data.get("item_2")
        week_index_raw = cleaned_data.get("week_index")

        if not item_1 and not item_2:
            raise ValidationError("Нужно выбрать хотя бы один предмет дропа.")

        if item_1 and item_2 and item_1.pk == item_2.pk:
            self.add_error("item_2", "Нельзя выбрать один и тот же предмет дважды.")

        if account and not self.access.staff_access and account.owner_id != self.user.id:
            self.add_error("account", "Можно выбирать только свои farm-аккаунты.")

        if account and week_index_raw:
            try:
                week_index = int(week_index_raw)
            except (TypeError, ValueError):
                week_index = None

            if week_index is not None and WeeklyDropLog.objects.filter(
                account=account,
                week_index=week_index,
            ).exists():
                self.add_error("week_index", "Для этого аккаунта дроп за выбранную неделю уже сохранён.")

        return cleaned_data

    def save(self, *, created_by=None) -> WeeklyDropLog:
        return create_weekly_drop(
            account=self.cleaned_data["account"],
            week_index=int(self.cleaned_data["week_index"]),
            item_1=self.cleaned_data.get("item_1"),
            item_2=self.cleaned_data.get("item_2"),
            notes=self.cleaned_data.get("notes") or "",
            created_by=created_by,
        )


class ActiveDropPoolEntryForm(forms.ModelForm):
    class Meta:
        model = ActiveDropPoolEntry
        fields = ["item", "sort_order", "note"]
        widgets = {
            "item": forms.Select(attrs={"class": "form-control"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control"}),
            "note": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = SteamItem.objects.filter(
            is_active=True,
            item_type__in=ALLOWED_DROP_ITEM_TYPES,
        ).order_by("item_type", "family_name", "market_hash_name")

    def clean_item(self):
        item = self.cleaned_data["item"]
        qs = ActiveDropPoolEntry.objects.filter(item=item)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Этот предмет уже находится в активном пуле.")
        return item


class FarmDropSessionForm(forms.ModelForm):
    class Meta:
        model = FarmDropSession
        fields = ["account", "source_type", "title", "collected_at", "notes"]
        widgets = {
            "account": forms.Select(attrs={"class": "form-control"}),
            "source_type": forms.Select(attrs={"class": "form-control"}),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: Weekly drop #12 или Bonus case",
                }
            ),
            "collected_at": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Короткая заметка по дропу: аккаунт, контекст, особенности продажи.",
                }
            ),
        }

    def __init__(self, *args, user, access, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.access = access
        qs = FarmAccount.objects.select_related("owner")
        if not access.staff_access:
            qs = qs.filter(owner=user)
        self.fields["account"].queryset = qs.order_by("sort_order", "title")
        self.fields["source_type"].initial = FarmDropSession.SourceType.WEEKLY
        self.fields["collected_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean_account(self):
        account = self.cleaned_data["account"]
        if not self.access.staff_access and account.owner_id != self.user.id:
            raise ValidationError("Можно использовать только свои farm-аккаунты.")
        return account


class FarmDropItemForm(forms.Form):
    steam_item = forms.ModelChoiceField(
        label="Каталожный предмет",
        queryset=SteamItem.objects.filter(is_active=True, item_type__in=ALLOWED_DROP_ITEM_TYPES).order_by("market_hash_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    market_hash_name = forms.CharField(
        label="Или market_hash_name вручную",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Например: Revolution Case",
            }
        ),
    )
    quantity = forms.IntegerField(
        label="Количество",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": 1,
                "placeholder": "1",
            }
        ),
    )
    collected_price_rub_snapshot = forms.DecimalField(
        label="Цена на момент дропа, ₽",
        min_value=0,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "placeholder": "Например: 125.00",
            }
        ),
    )
    notes = forms.CharField(
        label="Комментарий к позиции",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Например: выбито с бонусного аккаунта",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        steam_item = cleaned_data.get("steam_item")
        market_hash_name = (cleaned_data.get("market_hash_name") or "").strip()
        collected_price = cleaned_data.get("collected_price_rub_snapshot")
        notes = (cleaned_data.get("notes") or "").strip()

        is_empty_row = (
            not steam_item
            and not market_hash_name
            and collected_price in {None, ""}
            and not notes
        )
        if is_empty_row:
            cleaned_data["_empty"] = True
            return cleaned_data

        if not steam_item and not market_hash_name:
            raise ValidationError("Нужно выбрать каталожный предмет или ввести market_hash_name вручную.")

        cleaned_data["market_hash_name"] = market_hash_name
        cleaned_data["notes"] = notes
        cleaned_data["_empty"] = False
        return cleaned_data

    def to_payload(self) -> dict:
        steam_item = self.cleaned_data.get("steam_item")
        market_hash_name = (self.cleaned_data.get("market_hash_name") or getattr(steam_item, "market_hash_name", "") or "").strip()
        name_snapshot = getattr(steam_item, "name", "") or market_hash_name
        return {
            "steam_item": steam_item,
            "market_hash_name": market_hash_name,
            "name_snapshot": name_snapshot,
            "icon_url_snapshot": getattr(steam_item, "image_url", "") or "",
            "quantity": self.cleaned_data["quantity"],
            "collected_price_rub_snapshot": self.cleaned_data["collected_price_rub_snapshot"],
            "notes": self.cleaned_data.get("notes") or "",
        }


FarmDropItemFormSet = formset_factory(FarmDropItemForm, extra=2, min_num=1, validate_min=True, can_delete=True)


class FarmDropSaleForm(forms.ModelForm):
    class Meta:
        model = FarmDropSale
        fields = ["item", "platform", "sold_at", "quantity", "gross_sale_rub", "notes"]
        widgets = {
            "item": forms.Select(attrs={"class": "form-control"}),
            "platform": forms.Select(attrs={"class": "form-control"}),
            "sold_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "gross_sale_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Например: продажа через витрину CaseUP"}),
        }

    def __init__(self, *args, session: FarmDropSession, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self.user = user
        self.fields["item"].queryset = session.items.filter(remaining_quantity__gt=0).order_by("market_hash_name_snapshot")
        self.fields["platform"].initial = FarmDropSale.Platform.CASEUP
        self.fields["sold_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        config = get_farm_module_config()
        current_caseup_fee = resolve_sale_fee_percent(user=self.user, platform=FarmDropSale.Platform.CASEUP)
        self.fields["platform"].help_text = (
            "Комиссия рассчитывается сервисным слоем. "
            f"CaseUP сейчас: {current_caseup_fee:.2f}%, обычная: {config.default_caseup_fee_percent:.2f}%, "
            f"по farmer-плану: {config.subscribed_caseup_fee_percent:.2f}%."
        )

    def clean_item(self):
        item = self.cleaned_data["item"]
        if item.session_id != self.session.id:
            raise ValidationError("Предмет должен принадлежать этой сессии.")
        if item.owner_id != self.user.id and self.session.owner_id != self.user.id:
            raise ValidationError("Предмет не принадлежит текущему пользователю.")
        if item.remaining_quantity <= 0:
            raise ValidationError("По этой позиции уже ничего не осталось для продажи.")
        return item

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        quantity = cleaned_data.get("quantity")
        platform = cleaned_data.get("platform")
        if item and quantity and quantity > item.remaining_quantity:
            self.add_error("quantity", "Нельзя продать больше, чем осталось.")
        if platform:
            self.resolved_fee_percent = resolve_sale_fee_percent(user=self.user, platform=platform)
        else:
            self.resolved_fee_percent = None
        return cleaned_data


class FarmModuleConfigForm(forms.ModelForm):
    class Meta:
        model = FarmModuleConfig
        exclude = ["singleton_key", "created_at", "updated_at"]
        widgets = {
            "default_caseup_fee_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "subscribed_caseup_fee_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "external_reference_fee_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "price_up_threshold_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "price_down_threshold_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "caseup_advantage_min_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "milestone_notifications_enabled": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class FarmerRewardTierForm(forms.ModelForm):
    class Meta:
        model = FarmerRewardTier
        fields = [
            "code",
            "title",
            "threshold_turnover_rub",
            "reward_title",
            "reward_description",
            "bonus_value_rub",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "threshold_turnover_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "reward_title": forms.TextInput(attrs={"class": "form-control"}),
            "reward_description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "bonus_value_rub": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "step": 1}),
            "is_active": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
        }
