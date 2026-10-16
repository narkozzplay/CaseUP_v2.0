from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class RevenueCategory(models.TextChoices):
    COMMISSION = "commission", "Комиссии"
    SUBSCRIPTION = "subscription", "Подписки"
    STORE = "store", "Магазин"
    CASES = "cases", "Кейсы"


class RevenueSource(models.TextChoices):
    SWITCH_FEE = "switch_fee", "Смена тарифа подписки"
    TOPUP_FEE = "topup_fee", "Комиссия с пополнения"
    TRADEHUB = "tradehub", "Выкуп предметов (tradehub)"
    MARKET = "market", "Рынок"
    SUBSCRIPTION = "subscription", "Покупка подписки"
    STORE = "store", "Покупка в магазине"
    CASE_OPEN = "case_open", "Открытие кейсов"
    CASE_BUYBACK = "case_buyback", "Выкуп предметов из кейсов"


SOURCE_CATEGORY = {
    RevenueSource.SWITCH_FEE: RevenueCategory.COMMISSION,
    RevenueSource.TOPUP_FEE: RevenueCategory.COMMISSION,
    RevenueSource.TRADEHUB: RevenueCategory.COMMISSION,
    RevenueSource.MARKET: RevenueCategory.COMMISSION,
    RevenueSource.SUBSCRIPTION: RevenueCategory.SUBSCRIPTION,
    RevenueSource.STORE: RevenueCategory.STORE,
    RevenueSource.CASE_OPEN: RevenueCategory.CASES,
    RevenueSource.CASE_BUYBACK: RevenueCategory.CASES,
}


class RevenueEntry(models.Model):
    """One piece of platform income, written when the user pays for it.

    Top-ups are not income (the money still belongs to the user); income appears when the balance is
    spent on the site's services or a commission is taken. ``amount`` is signed: buying a case drop back
    is a negative row in the «cases» category. ``(source, ref)`` makes every write idempotent.
    """

    category = models.CharField(max_length=16, choices=RevenueCategory.choices, db_index=True)
    source = models.CharField(max_length=16, choices=RevenueSource.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="revenue_entries")
    ref = models.CharField(max_length=64, help_text="Исходная операция: ledger:<id>, tradehub:<id>, market:<deal>.")
    ledger_entry_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Revenue entry"
        verbose_name_plural = "Revenue entries"
        ordering = ("-occurred_at", "-id")
        constraints = [models.UniqueConstraint(fields=["source", "ref"], name="business_revenue_source_ref_uniq")]
        indexes = [
            models.Index(fields=["category", "occurred_at"], name="business_rev_cat_time_idx"),
            models.Index(fields=["source", "occurred_at"], name="business_rev_src_time_idx"),
        ]

    def __str__(self) -> str:
        return f"Revenue({self.source}, {self.amount}, {self.ref})"
