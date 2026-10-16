"""Notification kinds of the market."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_LISTING_MISSING = "market_listing_missing"
KIND_DEAL = "market_deal"

DEAL_TEXTS = {
    "new": ("Новая сделка {code}", "Купили «{name}» за {price} ₽. Отправьте обмен в Steam до {deadline} — деньги покупателя уже заморожены."),
    "sent": ("Продавец отправил трейд — {code}", "Примите обмен в Steam и нажмите «Трейд принят» на странице сделки."),
    "accepted": ("Покупатель принял трейд — {code}", "Деньги {price} ₽ удерживаются до {until} (защита обмена Steam), затем поступят вам."),
    "proof": ("Передача подтверждена — {code}", "Сайт увидел «{name}» в инвентаре покупателя. Через 7 дней удержания сделка закроется сама."),
    "deadline": ("Продавец не отправил трейд — {code}", "Срок отправки прошёл. Можно отменить сделку с полным возвратом денег или вызвать администрацию."),
    "cancelled": ("Сделка {code} отменена", "Причина: {reason}. Деньги возвращены покупателю."),
    "disputed": ("Спор по сделке {code}", "Вторая сторона вызвала администрацию. Таймеры сделки остановлены до решения."),
    "dispute_admin": ("Нужно решение: спор {code}", "«{name}» за {price} ₽. Откройте сделку и примите решение."),
    "resolved": ("Спор {code} решён", "Решение администрации: {decision}."),
    "completed": ("Сделка {code} завершена", "«{name}» — {price} ₽ зачислены продавцу."),
}


class _Safe(dict):
    def __missing__(self, key):
        return ""


def render_listing_missing(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    code, name = payload.get("code", ""), payload.get("name", "")
    if lang == "ru":
        title = f"Лот {code} снят с рынка"
        body = f"{name}: предмета больше нет в вашем инвентаре Steam, поэтому лот убран с витрины."
    else:
        title = f"Listing {code} was removed"
        body = f"{name} is no longer in your Steam inventory, so the listing was taken off the market."
    return NotificationView(title=title, body=body, url=reverse("market:sell"), icon="market")


def render_deal(notification, lang: str) -> NotificationView:
    payload = _Safe(notification.payload or {})
    title, body = DEAL_TEXTS.get(payload.get("event"), ("Сделка {code}", "Статус сделки изменился."))
    code = payload.get("code", "")
    return NotificationView(title=title.format_map(payload), body=body.format_map(payload), url=reverse("market:deal", args=[code]) if code else reverse("market:deals"), icon="market")


def register_notification_kinds() -> None:
    register_kind(KIND_LISTING_MISSING, render_listing_missing)
    register_kind(KIND_DEAL, render_deal)
