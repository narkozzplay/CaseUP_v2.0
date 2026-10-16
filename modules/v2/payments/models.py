from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Создан"
        PENDING = "pending", "Ожидает оплаты"
        SUCCEEDED = "succeeded", "Оплачен"
        CANCELED = "canceled", "Отменён"
        FAILED = "failed", "Ошибка"

    FINAL_STATUSES = (Status.SUCCEEDED, Status.CANCELED, Status.FAILED)

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    provider = models.CharField(max_length=32)
    # What the payer is charged: credit_amount (lands on the balance) + fee_amount (the top-up commission).
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    credit_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # The provider's own fee (amount minus what it pays out), known once the payment succeeded.
    provider_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="RUB")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    external_id = models.CharField(max_length=128, blank=True, default="")
    confirmation_url = models.URLField(max_length=1000, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    error_message = models.CharField(max_length=255, blank=True, default="")
    provider_payload = models.JSONField(default=dict, blank=True)
    # Paid in the provider's test mode (test shop / test card): no real money moved.
    is_test = models.BooleanField(default=False)
    # User agreement version the payer accepted for this top-up (frontend.legal.AGREEMENT_VERSION).
    terms_version = models.CharField(max_length=32, blank=True, default="")
    ledger_entry = models.OneToOneField(
        "profiles.LedgerEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                condition=~models.Q(external_id=""),
                name="payments_payment_provider_external_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Payment({self.public_id}, {self.provider}, {self.amount} {self.currency}, {self.status})"

    @property
    def is_final(self) -> bool:
        return self.status in self.FINAL_STATUSES

    @property
    def balance_amount(self):
        """What the top-up puts on the balance (old payments had no commission)."""
        return self.credit_amount if self.credit_amount is not None else self.amount

    @property
    def is_credited(self) -> bool:
        return self.ledger_entry_id is not None
