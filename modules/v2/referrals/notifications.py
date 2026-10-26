"""Notification kinds emitted by the referral program."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_REFERRAL_COUNTED = "referral_counted"
KIND_REFERRAL_WELCOME_BONUS = "referral_welcome_bonus"


def render_referral_counted(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    referee, count = payload.get("referee", ""), payload.get("count", 0)
    discount, turnover = payload.get("discount", 0), payload.get("turnover", 0)
    if lang == "ru":
        title = f"Реферал засчитан: {referee}"
        body = f"Засчитано рефералов: {count}. Скидка на подписки — {discount}%."
        if turnover:
            body += f" Бонус с продаж рефералов — {turnover}%."
    else:
        title = f"Referral counted: {referee}"
        body = f"Counted referrals: {count}. Subscription discount: {discount}%."
        if turnover:
            body += f" Turnover bonus: {turnover}%."
    return NotificationView(title=title, body=body, url=reverse("referrals:dashboard"), icon="referrals")


def _date(raw: str) -> str:
    from datetime import datetime

    from django.utils import timezone

    try:
        return timezone.localtime(datetime.fromisoformat(raw)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def render_referral_welcome_bonus(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    days, plan = payload.get("days", 0), payload.get("plan") or "Плюс"
    other = payload.get("other") or payload.get("other_username", "")
    status, role, mode = payload.get("status"), payload.get("role"), payload.get("mode")
    until, converted = _date(payload.get("expires_at", "")), payload.get("converted_days", 0)
    url = reverse("referrals:dashboard")
    if lang != "ru":
        if status == "granted":
            title = f"+{days} days of {plan}" + (" for inviting a friend" if role == "referrer" else " as a welcome gift")
            body = f"{'You invited' if role == 'referrer' else 'Invited by'} {other}." + (f" Active until {until}." if until else "")
        elif status == "held":
            title, body = f"+{days} days of {plan}: under review", "The bonus will be credited after a quick check by an administrator."
        else:
            title, body = "Referral bonus was not credited", payload.get("reason", "")
        return NotificationView(title=title, body=body, url=url, icon="referrals")

    if status == "granted":
        if role == "referrer":
            title = f"+{days} дней {plan} за приглашение"
            body = f"{other} вошёл на сайт через Steam по вашей ссылке."
        else:
            title = f"+{days} дней {plan} в подарок"
            body = f"Вас пригласил {other} — подарок для вас обоих."
        if mode == "extended":
            body += f" Ваша подписка продлена до {until}."
        elif mode == "upgraded":
            body += f" {plan} действует до {until}" + (f", остаток прежнего тарифа зачтён (+{converted} дн.)." if converted else ".")
        elif mode == "unlimited":
            body += " У вас бессрочная подписка, продлевать нечего."
        elif until:
            body += f" {plan} действует до {until}."
    elif status == "held":
        title = f"+{days} дней {plan}: бонус на проверке"
        body = "Начислим после короткой проверки администратором — так мы защищаемся от накруток."
    else:
        title = "Бонус за приглашение не начислен"
        body = payload.get("reason", "")
    return NotificationView(title=title, body=body, url=url, icon="referrals")


def register_notification_kinds() -> None:
    register_kind(KIND_REFERRAL_COUNTED, render_referral_counted)
    register_kind(KIND_REFERRAL_WELCOME_BONUS, render_referral_welcome_bonus)
