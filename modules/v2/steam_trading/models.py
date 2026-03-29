from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem


ZERO = Decimal("0.00")


class SteamPurchase(models.Model):
    item = models.ForeignKey(
        SteamItem,
        on_delete=models.PROTECT,
        related_name="trading_purchases",
    )
    purchased_at = models.DateTimeField(db_index=True)
    unlocked_at = models.DateTimeField(db_index=True, null=True, blank=True)
    unlock_notified_at = models.DateTimeField(null=True, blank=True, db_index=True)
    unlock_notification_error = models.TextField(blank=True, default="")
    quantity = models.PositiveIntegerField()
    purchase_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    total_spent_rub = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="steam_trading_purchases",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-purchased_at", "-id"]
        indexes = [
            models.Index(fields=["created_by", "purchased_at"], name="steam_tradi_created_3ef912_idx"),
            models.Index(fields=["item", "purchased_at"], name="steam_tradi_item_id_e7dfbe_idx"),
            models.Index(fields=["created_at"], name="steam_tradi_created_908ff8_idx"),
            models.Index(fields=["unlocked_at", "unlock_notified_at"], name="steam_tradi_unlocke_1e912d_idx"),
        ]

    def __str__(self) -> str:
        return f"Purchase({self.item_id}, qty={self.quantity}, spent={self.total_spent_rub})"

    def clean(self):
        super().clean()
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError({"quantity": "Количество должно быть больше нуля."})
        if self.purchase_price_rub is None or self.purchase_price_rub < 0:
            raise ValidationError({"purchase_price_rub": "Цена покупки не может быть отрицательной."})
        if not self.purchased_at:
            raise ValidationError({"purchased_at": "Укажите дату и время покупки."})
        if self.item_id and self.item.item_type != SteamItem.ItemType.CASE:
            raise ValidationError({"item": "Для MVP доступны только case-предметы."})
        if self.item_id and not self.item.is_active:
            raise ValidationError({"item": "Можно выбирать только активные предметы каталога."})

    @staticmethod
    def calculate_unlocked_at(purchased_at):
        if purchased_at is None:
            return None

        target_tz = ZoneInfo(settings.TIME_ZONE)
        aware_purchase = purchased_at
        if timezone.is_naive(aware_purchase):
            aware_purchase = timezone.make_aware(aware_purchase, target_tz)

        local_purchase = timezone.localtime(aware_purchase, target_tz)
        unlock_date = local_purchase.date() + timedelta(days=7)
        return datetime.combine(unlock_date, time(hour=10, minute=0, tzinfo=target_tz))

    def recalculate_total_spent(self) -> Decimal:
        quantity = Decimal(self.quantity or 0)
        price = self.purchase_price_rub or ZERO
        return (quantity * price).quantize(Decimal("0.01"))

    def get_sold_quantity_total(self, *, exclude_sale_id: int | None = None) -> int:
        sales_qs = self.sales.all()
        if exclude_sale_id is not None:
            sales_qs = sales_qs.exclude(pk=exclude_sale_id)
        return int(sales_qs.aggregate(total=Sum("quantity")).get("total") or 0)

    @property
    def sold_quantity_total(self) -> int:
        annotated_value = getattr(self, "sold_quantity_total_annotated", None)
        if annotated_value is not None:
            return int(annotated_value or 0)
        return self.get_sold_quantity_total()

    @property
    def remaining_quantity(self) -> int:
        return max(int(self.quantity or 0) - self.sold_quantity_total, 0)

    @property
    def realized_profit_rub(self) -> Decimal:
        annotated_value = getattr(self, "realized_profit_rub_annotated", None)
        if annotated_value is not None:
            return Decimal(annotated_value or ZERO).quantize(Decimal("0.01"))
        total = self.sales.aggregate(total=Sum("profit_rub")).get("total") or ZERO
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def sale_status(self) -> str:
        if self.sold_quantity_total <= 0:
            return "not_sold"
        if self.remaining_quantity <= 0:
            return "sold_full"
        return "sold_partial"

    @property
    def sale_status_label(self) -> str:
        return {
            "not_sold": "Не продавалось",
            "sold_partial": "Продано частично",
            "sold_full": "Продано полностью",
        }[self.sale_status]

    @property
    def can_sell(self) -> bool:
        return self.is_unlocked and self.remaining_quantity > 0

    def save(self, *args, **kwargs):
        self.total_spent_rub = self.recalculate_total_spent()
        self.unlocked_at = self.calculate_unlocked_at(self.purchased_at)
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def current_price_rub(self) -> Decimal:
        return self.item.price_rub or ZERO

    @property
    def current_total_rub(self) -> Decimal:
        return (Decimal(self.quantity) * self.current_price_rub).quantize(Decimal("0.01"))

    @property
    def profit_rub(self) -> Decimal:
        return (self.current_total_rub - (self.total_spent_rub or ZERO)).quantize(Decimal("0.01"))

    @property
    def profit_percent(self) -> Decimal:
        if not self.total_spent_rub:
            return ZERO
        return ((self.profit_rub / self.total_spent_rub) * Decimal("100")).quantize(Decimal("0.01"))

    @property
    def is_unlocked(self) -> bool:
        if self.unlocked_at is None:
            return False
        return self.unlocked_at <= timezone.now()


class SteamPurchaseSale(models.Model):
    purchase = models.ForeignKey(
        SteamPurchase,
        on_delete=models.CASCADE,
        related_name="sales",
    )
    sold_at = models.DateTimeField(db_index=True)
    quantity = models.PositiveIntegerField()
    sale_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    total_sale_rub = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    cost_basis_rub = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    profit_rub = models.DecimalField(max_digits=14, decimal_places=2, default="0.00")
    profit_percent = models.DecimalField(max_digits=7, decimal_places=2, default="0.00")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="steam_trading_purchase_sales",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sold_at", "-id"]
        indexes = [
            models.Index(fields=["purchase", "sold_at"], name="st_trd_purch_1d2b24_idx"),
            models.Index(fields=["created_by", "sold_at"], name="steam_tradi_created_ab09f8_idx"),
            models.Index(fields=["created_at"], name="steam_tradi_created_c48f14_idx"),
        ]

    def __str__(self) -> str:
        return f"PurchaseSale(purchase={self.purchase_id}, qty={self.quantity}, total={self.total_sale_rub})"

    def clean(self):
        super().clean()
        errors = {}
    
        if not self.purchase_id:
            errors[NON_FIELD_ERRORS] = "Не найдена закупка для продажи."
    
        if not self.sold_at:
            errors["sold_at"] = "Укажите дату и время продажи."
    
        if self.quantity is None or self.quantity <= 0:
            errors["quantity"] = "Количество продажи должно быть больше нуля."
    
        if self.sale_price_rub is None or self.sale_price_rub < 0:
            errors["sale_price_rub"] = "Цена продажи не может быть отрицательной."
    
        if self.purchase_id:
            if not self.purchase.is_unlocked:
                errors[NON_FIELD_ERRORS] = "Нельзя продавать закупку, пока она в трейд-бане."
    
            sold_total = self.purchase.get_sold_quantity_total(exclude_sale_id=self.pk)
            remaining = max(int(self.purchase.quantity or 0) - sold_total, 0)
            if self.quantity and self.quantity > remaining:
                errors["quantity"] = f"Нельзя продать больше остатка. Доступно: {remaining}."
    
        if errors:
            raise ValidationError(errors)

    def recalculate_snapshots(self) -> None:
        quantity = Decimal(self.quantity or 0)
        sale_price = self.sale_price_rub or ZERO
        purchase_price = self.purchase.purchase_price_rub if self.purchase_id else ZERO

        self.total_sale_rub = (quantity * sale_price).quantize(Decimal("0.01"))
        self.cost_basis_rub = (quantity * purchase_price).quantize(Decimal("0.01"))
        self.profit_rub = (self.total_sale_rub - self.cost_basis_rub).quantize(Decimal("0.01"))
        if self.cost_basis_rub == ZERO:
            self.profit_percent = ZERO
        else:
            self.profit_percent = ((self.profit_rub / self.cost_basis_rub) * Decimal("100")).quantize(Decimal("0.01"))

    @property
    def result_label(self) -> str:
        if self.profit_rub > ZERO:
            return "Сделка в плюс"
        if self.profit_rub < ZERO:
            return "Сделка в минус"
        return "Сделка в ноль"

    def save(self, *args, **kwargs):
        self.recalculate_snapshots()
        self.full_clean()
        return super().save(*args, **kwargs)
