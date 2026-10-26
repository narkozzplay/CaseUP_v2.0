"""Which notification kinds go to which push category (the user can switch categories off)."""
from __future__ import annotations

CATEGORIES = {
    "market": "Рынок: покупки, трейды и споры",
    "messages": "Личные сообщения",
    "balance": "Баланс: пополнения, бонусы, рефералы",
    "security": "Безопасность: новые входы и защита аккаунта",
    "daily_bonus": "Напоминание о бонусе дня",
    "friends": "Друзья: заявки",
    "alerts": "Алерты цен",
    "investments": "Инвестиции: снятие трейд-бана",
    "farm": "Ферма: продажи, цены, бонусы",
    "support": "Поддержка: ответы на обращения",
}

KIND_CATEGORY = {
    "market_deal": "market",
    "market_listing_missing": "market",
    "balance_topup_succeeded": "balance",
    "promo_bonus": "balance",
    "referral_counted": "balance",
    "referral_welcome_bonus": "balance",
    "account_new_login": "security",
    "account_2fa_locked": "security",
    "antifraud_multi_account": "security",
    "daily_bonus_reminder": "daily_bonus",
    "donation_sponsor_granted": "balance",
    "friend_request": "friends",
    "friend_request_accepted": "friends",
    "steam_price_alert": "alerts",
    "steam_investment_unlocked": "investments",
    "farm_alert": "farm",
    "support_reply": "support",
    "support_status": "support",
    "support_staff": "support",
    "support_new": "support",
}

# Pushes are short-lived: a reminder that arrives a day late is noise.
CATEGORY_TTL = {"daily_bonus": 6 * 3600, "messages": 24 * 3600}
DEFAULT_TTL = 3 * 24 * 3600


def category_for(kind: str) -> str:
    return KIND_CATEGORY.get(kind, "other")
