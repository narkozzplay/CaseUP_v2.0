"""Site-wide activity journal (Control Center → Логи).

Events come from three places:
- domain receivers (control_center.activity_events) for meaningful actions: sign-in, balance, payments,
  cases, purchases, social, admin actions...;
- ActivityContextMiddleware for every state-changing HTTP request (method, route, status; never bodies);
- the request context, so receivers know who acted and from which IP without being passed a request.

Writing to the journal must never break the action being logged: every write runs in its own savepoint
and swallows its own errors.
"""
from __future__ import annotations

import ipaddress
import logging
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import ActivityCategory, ActivityLog


logger = logging.getLogger(__name__)

_current_request: ContextVar = ContextVar("caseup_activity_request", default=None)

# Human-readable labels for the event codes used by the receivers (unknown codes are shown as is).
EVENT_LABELS = {
    "auth.login": "Вход",
    "auth.logout": "Выход",
    "auth.login_failed": "Неудачный вход",
    "account.registered": "Регистрация",
    "account.consent": "Принятие документов",
    "account.verification_requested": "Заявка на верификацию",
    "account.verification_reviewed": "Решение по верификации",
    "account.customization": "Оформление профиля",
    "account.privacy": "Настройки приватности",
    "account.session_ended": "Завершён сеанс",
    "account.sessions_ended_others": "Завершены другие сеансы",
    "account.2fa_enabled": "2FA включена",
    "account.2fa_disabled": "2FA отключена",
    "account.2fa_failed": "Неверный код 2FA",
    "account.2fa_backup_used": "Вход по резервному коду",
    "account.2fa_backup_regenerated": "Новые резервные коды 2FA",
    "balance.topup": "Пополнение баланса",
    "balance.case_open": "Списание за кейс",
    "balance.item_sale": "Зачисление за продажу",
    "balance.store_purchase": "Списание: магазин",
    "balance.bundle_purchase": "Списание: набор",
    "balance.referral_bonus": "Бонус за рефералов",
    "balance.daily_bonus": "Ежедневный бонус",
    "market.trade_link": "Рынок: трейд-ссылка",
    "market.inventory": "Рынок: инвентарь",
    "market.listed": "Рынок: лот выставлен",
    "market.price": "Рынок: цена изменена",
    "market.cancelled": "Рынок: лот снят",
    "market.missing": "Рынок: лот снят (нет предмета)",
    "market.blocked": "Рынок: лот снят админом",
    "market.deal_created": "Рынок: сделка открыта",
    "market.deal_sent": "Рынок: трейд отправлен",
    "market.deal_accepted": "Рынок: трейд принят",
    "market.deal_proof": "Рынок: передача подтверждена",
    "market.deal_completed": "Рынок: сделка завершена",
    "market.deal_cancelled": "Рынок: сделка отменена",
    "market.deal_disputed": "Рынок: вызов админа",
    "market.deal_resolved": "Рынок: спор решён",
    "market.deal_chat_viewed": "Рынок: админ открыл чат сделки",
    "balance.market_purchase": "Списание: покупка на рынке",
    "balance.market_refund": "Возврат по сделке",
    "balance.market_sale": "Зачисление: продажа на рынке",
    "balance.market_payout": "Списание: вывод с рынка",
    "referral.bound": "Регистрация по приглашению",
    "referral.qualified": "Реферал пополнил баланс",
    "referral.flagged": "Реферал на проверке",
    "promo.activated": "Активация промокода",
    "balance.promo_bonus": "Бонус по промокоду",
    "balance.subscription_purchase": "Списание: подписка",
    "balance.theme_purchase": "Списание: тема",
    "balance.admin_adjust": "Корректировка баланса",
    "balance.withdraw_lock": "Блокировка на вывод",
    "balance.withdraw_cancel": "Отмена вывода",
    "payment.created": "Платёж создан",
    "payment.succeeded": "Платёж оплачен",
    "payment.canceled": "Платёж отменён",
    "payment.failed": "Платёж не прошёл",
    "case.open": "Открытие кейса",
    "case.item_sold": "Продажа предмета",
    "case.terminal_started": "Терминал: старт",
    "case.terminal_accepted": "Терминал: принят",
    "case.terminal_declined": "Терминал: отказ",
    "store.purchase": "Покупка в магазине",
    "store.bundle_purchase": "Покупка набора",
    "store.review": "Отзыв о товаре",
    "subscription.assigned": "Подписка выдана",
    "subscription.status": "Статус подписки",
    "trading.investment_purchase": "Инвестиции: покупка",
    "trading.investment_sale": "Инвестиции: продажа",
    "trading.order_created": "TradeHub: заявка",
    "trading.order_status": "TradeHub: статус заявки",
    "farm.account_added": "Ферма: аккаунт добавлен",
    "farm.account_removed": "Ферма: аккаунт удалён",
    "farm.drop_session": "Ферма: сессия дропа",
    "farm.drop_sale": "Ферма: продажа дропа",
    "social.friend_request": "Заявка в друзья",
    "social.friend_request_status": "Ответ на заявку в друзья",
    "social.friend_removed": "Удаление из друзей",
    "social.user_blocked": "Блокировка пользователя",
    "social.user_unblocked": "Разблокировка пользователя",
    "social.space_created": "Создано сообщество / беседа",
    "social.space_joined": "Вступление в сообщество",
    "social.message": "Сообщение",
    "constructor.template_created": "Конструктор: шаблон",
    "constructor.version_moderated": "Конструктор: модерация",
    "security.ban": "Бан",
    "security.unban": "Снятие бана",
}


def bind_request(request):
    return _current_request.set(request)


def unbind_request(token) -> None:
    _current_request.reset(token)


def current_request():
    return _current_request.get()


def client_ip(request) -> str | None:
    from modules.v2.payments.ip_allowlist import get_client_ip

    try:
        return str(ipaddress.ip_address((get_client_ip(request) or "").strip()))
    except ValueError:
        return None


def authenticated_user(user):
    return user if getattr(user, "is_authenticated", False) and getattr(user, "pk", None) else None


def log_activity(
    *,
    category: str,
    event: str,
    summary: str = "",
    user=None,
    actor=None,
    amount=None,
    obj=None,
    payload: dict | None = None,
    request=None,
    method: str = "",
    path: str = "",
    status_code: int | None = None,
) -> ActivityLog | None:
    """Write one journal entry. Never raises; returns None when the write failed."""
    request = request or current_request()
    request_user = authenticated_user(getattr(request, "user", None)) if request is not None else None
    actor = authenticated_user(actor) or request_user
    if not getattr(user, "pk", None):
        user = actor
    try:
        with transaction.atomic():
            return ActivityLog.objects.create(
                category=category if category in ActivityCategory.values else ActivityCategory.REQUEST,
                event=event[:64],
                summary=(summary or "")[:255],
                user=user if getattr(user, "pk", None) else None,
                username=(getattr(user, "username", "") or "")[:150],
                actor=actor,
                actor_username=(getattr(actor, "username", "") or "")[:150],
                amount=Decimal(str(amount)).quantize(Decimal("0.01")) if amount is not None else None,
                object_type=(obj._meta.label if obj is not None else "")[:64],
                object_id=str(getattr(obj, "pk", "") or "")[:64],
                payload=payload or {},
                ip_address=client_ip(request) if request is not None else None,
                user_agent=((request.META.get("HTTP_USER_AGENT") or "") if request is not None else "")[:255],
                method=method[:8],
                path=path[:255],
                status_code=status_code,
            )
    except Exception:  # noqa: BLE001 - the journal must never break the action it describes
        logger.exception("Activity log write failed for %s", event)
        return None


def event_label(event: str) -> str:
    return EVENT_LABELS.get(event, event)


def prune_activity_log(*, now=None) -> dict[str, int]:
    now = now or timezone.now()
    request_days = int(getattr(settings, "ACTIVITY_LOG_REQUEST_RETENTION_DAYS", 90))
    event_days = int(getattr(settings, "ACTIVITY_LOG_RETENTION_DAYS", 365))
    requests_deleted, _ = ActivityLog.objects.filter(
        category=ActivityCategory.REQUEST, created_at__lt=now - timedelta(days=request_days)
    ).delete()
    events_deleted, _ = (
        ActivityLog.objects.exclude(category=ActivityCategory.REQUEST)
        .filter(created_at__lt=now - timedelta(days=event_days))
        .delete()
    )
    return {"requests": requests_deleted, "events": events_deleted}
