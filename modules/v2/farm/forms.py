from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import formset_factory
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem

from .models import (
    STATUS_LABELS_RU,
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


RU_STATUS_CHOICES = [(value, STATUS_LABELS_RU.get(value, label)) for value, label in FarmAccount.Status.choices]


class FarmAccountForm(forms.ModelForm):
    """Connect or edit one farm account. A trade link is the main input:
    SteamID64 is derived from it and the title falls back to "Bot NN"."""

    class Meta:
        model = FarmAccount
        fields = [
            "trade_url",
            "title",
            "role",
            "status",
            "steam_id64",
            "login_hint",
            "notes",
        ]
        labels = {
            "trade_url": "Трейд-ссылка",
            "title": "Название",
            "role": "Роль",
            "status": "Статус",
            "steam_id64": "SteamID64",
            "login_hint": "Логин / подсказка",
            "notes": "Заметки",
        }
        help_texts = {
            "trade_url": "Steam → Инвентарь → Предложения обмена → «Кто может отправлять мне предложения». SteamID определится сам.",
            "title": "Можно оставить пустым: возьмём имя из Steam или назовём Bot NN.",
            "role": "Фарм-бот собирает недельный дроп, склад принимает его (для будущей авто-отправки).",
            "steam_id64": "Нужен, только если трейд-ссылки пока нет.",
        }
        widgets = {
            "trade_url": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://steamcommunity.com/tradeoffer/new/?partner=…&token=…",
                    "autocomplete": "off",
                    "data-farm-trade-input": "true",
                }
            ),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Bot 01"}),
            "steam_id64": forms.TextInput(attrs={"class": "form-control", "placeholder": "7656119…", "data-farm-steamid-input": "true"}),
            "login_hint": forms.TextInput(attrs={"class": "form-control", "placeholder": "Не храни здесь пароли"}),
            "role": forms.RadioSelect(attrs={"class": "farm-choice__input"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["title"].required = False
        self.fields["role"].required = False
        self.fields["status"].choices = RU_STATUS_CHOICES
        self.fields["status"].required = False
        if not self.instance.pk:
            self.fields["status"].initial = FarmAccount.Status.ACTIVE

    def clean(self):
        cleaned_data = super().clean()

        if not cleaned_data.get("role"):
            cleaned_data["role"] = self.instance.role or FarmAccount.Role.FARM
            self.instance.role = cleaned_data["role"]
        if not cleaned_data.get("status"):
            cleaned_data["status"] = self.instance.status if self.instance.pk else FarmAccount.Status.ACTIVE
            self.instance.status = cleaned_data["status"]

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
                    "SteamID64 не совпадает с partner из трейд-ссылки.",
                )
                return cleaned_data
            steam_id64 = parsed.steam_id64
            cleaned_data["steam_id64"] = steam_id64
        elif not steam_id64:
            self.add_error("trade_url", "Вставь трейд-ссылку аккаунта или укажи SteamID64.")
            return cleaned_data
        elif not (steam_id64.isdigit() and len(steam_id64) == 17):
            self.add_error("steam_id64", "SteamID64 — это 17 цифр, начинается с 7656.")
            return cleaned_data

        owner = self.owner or getattr(self.instance, "owner", None)
        if owner is not None:
            from .connect import find_duplicate_account

            duplicate = find_duplicate_account(owner, steam_id64, exclude_id=self.instance.pk)
            if duplicate is not None:
                self.add_error("trade_url", f"Этот аккаунт уже подключён как «{duplicate.title}».")

        return cleaned_data


class FarmAccountImportForm(forms.Form):
    accounts_text = forms.CharField(
        label="Аккаунты списком",
        widget=forms.Textarea(
            attrs={
                "class": "form-control farm-import__textarea",
                "rows": 8,
                "spellcheck": "false",
                "placeholder": "Bot 01; https://steamcommunity.com/tradeoffer/new/?partner=…&token=…\nhttps://steamcommunity.com/tradeoffer/new/?partner=…&token=…",
                "data-farm-import-input": "true",
            }
        ),
        help_text="По одному аккаунту в строке: «название; трейд-ссылка» или просто трейд-ссылка.",
    )
    role = forms.ChoiceField(
        label="Роль",
        choices=FarmAccount.Role.choices,
        initial=FarmAccount.Role.FARM,
        widget=forms.RadioSelect(attrs={"class": "farm-choice__input"}),
    )
    status = forms.ChoiceField(
        label="Статус",
        choices=RU_STATUS_CHOICES,
        initial=FarmAccount.Status.ACTIVE,
        widget=forms.Select(attrs={"class": "form-control"}),
    )


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
            "icon_url_snapshot": getattr(steam_item, "icon_url", "") or "",
            "quantity": self.cleaned_data["quantity"],
            "collected_price_rub_snapshot": self.cleaned_data["collected_price_rub_snapshot"],
            "notes": self.cleaned_data.get("notes") or "",
        }


FarmDropItemFormSet = formset_factory(FarmDropItemForm, extra=1, min_num=1, validate_min=True, can_delete=True)


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


MAX_DROP_ENTRY_ITEMS = 12
MAX_DROP_ITEM_QUANTITY = 999


def _parse_quantity(raw) -> int | None:
    try:
        value = int(str(raw or "1").strip() or "1")
    except (TypeError, ValueError):
        return None
    if value < 1 or value > MAX_DROP_ITEM_QUANTITY:
        return None
    return value


def _parse_price(raw):
    from decimal import Decimal, InvalidOperation

    text = str(raw or "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None, True
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    if value < 0 or value >= Decimal("10000000000"):
        return None, False
    return value.quantize(Decimal("0.01")), True


def parse_drop_entry_items(data) -> tuple[list[dict], list[str]]:
    """Reads drop positions from the quick drop form.

    Catalog positions come as repeated `item=<SteamItem id>` with
    `qty_<id>` / `price_<id>`; positions without a catalog match come as
    parallel `manual_name` / `manual_qty` / `manual_price` lists."""
    payloads: list[dict] = []
    errors: list[str] = []

    item_ids: list[int] = []
    for raw in data.getlist("item"):
        raw = str(raw).strip()
        if raw.isdigit() and int(raw) not in item_ids:
            item_ids.append(int(raw))

    items = SteamItem.objects.filter(
        pk__in=item_ids,
        is_active=True,
        item_type__in=ALLOWED_DROP_ITEM_TYPES,
    ).in_bulk()

    for item_id in item_ids:
        item = items.get(item_id)
        if item is None:
            errors.append("Один из выбранных предметов недоступен для дропа.")
            continue
        quantity = _parse_quantity(data.get(f"qty_{item_id}"))
        if quantity is None:
            errors.append(f"{item.market_hash_name}: количество от 1 до {MAX_DROP_ITEM_QUANTITY}.")
            continue
        price, price_ok = _parse_price(data.get(f"price_{item_id}"))
        if not price_ok:
            errors.append(f"{item.market_hash_name}: некорректная цена.")
            continue
        if price is None:
            price = item.price_rub or 0
        payloads.append(
            {
                "steam_item": item,
                "market_hash_name": item.market_hash_name,
                "name_snapshot": item.name or item.market_hash_name,
                "icon_url_snapshot": item.icon_url or "",
                "quantity": quantity,
                "collected_price_rub_snapshot": price,
                "notes": "",
            }
        )

    names = data.getlist("manual_name")
    quantities = data.getlist("manual_qty")
    prices = data.getlist("manual_price")
    for index, raw_name in enumerate(names):
        name = str(raw_name or "").strip()[:255]
        if not name:
            continue
        quantity = _parse_quantity(quantities[index] if index < len(quantities) else "1")
        if quantity is None:
            errors.append(f"{name}: количество от 1 до {MAX_DROP_ITEM_QUANTITY}.")
            continue
        price, price_ok = _parse_price(prices[index] if index < len(prices) else "")
        if not price_ok:
            errors.append(f"{name}: некорректная цена.")
            continue
        matched = SteamItem.objects.filter(market_hash_name=name, is_active=True).first()
        payloads.append(
            {
                "steam_item": matched,
                "market_hash_name": name,
                "name_snapshot": getattr(matched, "name", "") or name,
                "icon_url_snapshot": getattr(matched, "icon_url", "") or "",
                "quantity": quantity,
                "collected_price_rub_snapshot": price if price is not None else (getattr(matched, "price_rub", None) or 0),
                "notes": "",
            }
        )

    if not payloads and not errors:
        errors.append("Выбери хотя бы один предмет дропа.")
    if len(payloads) > MAX_DROP_ENTRY_ITEMS:
        errors.append(f"Не больше {MAX_DROP_ENTRY_ITEMS} позиций в одном дропе.")
    return payloads, errors
