"""Plan quotas. A plan stores overrides in SubscriptionPlan.limits; anything it leaves out uses the free quota."""

PRICE_ALERTS = "price_alerts"

FREE_LIMITS: dict[str, int | None] = {
    PRICE_ALERTS: 3,
}

LIMIT_LABELS = {
    PRICE_ALERTS: "Ценовые алерты",
}
