"""What the Notification Center shows: which notifications exist, where they go and their latest activity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Max
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.webpush.categories import CATEGORIES as PUSH_CATEGORIES

SITE = "site"
PUSH = "push"
TELEGRAM = "telegram"

CHANNEL_LABELS = {SITE: "Сайт", PUSH: "Пуш", TELEGRAM: "Telegram"}


@dataclass(frozen=True)
class NotificationGroup:
    key: str
    title: str
    description: str
    icon: str
    channels: tuple[str, ...]
    kinds: tuple[str, ...] = ()
    push_category: str = ""
    url_name: str = ""
    module: str = ""


USER_GROUPS = (
    NotificationGroup(
        key="market",
        title="Рынок",
        description="Покупки и продажи, трейды, споры и лоты, которые пропали из инвентаря.",
        icon="market",
        channels=(SITE, PUSH),
        kinds=("market_deal", "market_listing_missing"),
        push_category="market",
        url_name="market:index",
        module="market",
    ),
    NotificationGroup(
        key="messages",
        title="Личные сообщения",
        description="Новое сообщение в личном чате. На сайте — счётчик в кнопке «Чаты».",
        icon="chats",
        channels=(PUSH,),
        push_category="messages",
        url_name="social:chats",
    ),
    NotificationGroup(
        key="friends",
        title="Друзья",
        description="Заявки в друзья и принятые заявки — принять можно прямо из уведомления.",
        icon="friends",
        channels=(SITE, PUSH),
        kinds=("friend_request", "friend_request_accepted"),
        push_category="friends",
        url_name="social_friends:friends",
    ),
    NotificationGroup(
        key="balance",
        title="Баланс и бонусы",
        description="Пополнения, промокоды, награды за рефералов, приветственный бонус и статус спонсора за донат.",
        icon="balance_topup",
        channels=(SITE, PUSH),
        kinds=("balance_topup_succeeded", "promo_bonus", "referral_counted", "referral_welcome_bonus", "donation_sponsor_granted"),
        push_category="balance",
        url_name="referrals:dashboard",
    ),
    NotificationGroup(
        key="subscriptions",
        title="Подписка",
        description="Подписка скоро закончится, закончилась или вам её подарили.",
        icon="subscriptions",
        channels=(SITE, PUSH),
        kinds=("subscription_expiring", "subscription_expired", "subscription_gift"),
        url_name="subscriptions:plans",
        module="subscriptions",
    ),
    NotificationGroup(
        key="security",
        title="Безопасность",
        description="Вход в аккаунт с нового устройства и блокировка 2FA после неверных кодов.",
        icon="shield",
        channels=(SITE, PUSH),
        kinds=("account_new_login", "account_2fa_locked"),
        push_category="security",
    ),
    NotificationGroup(
        key="daily_bonus",
        title="Бонус дня",
        description="Напоминание, что бонус дня можно забрать.",
        icon="daily_bonus",
        channels=(SITE, PUSH),
        kinds=("daily_bonus_reminder",),
        push_category="daily_bonus",
        url_name="daily_bonus:page",
    ),
    NotificationGroup(
        key="alerts",
        title="Алерты цен",
        description="Ваше правило price alerts сработало: цена выросла или упала сильнее порога.",
        icon="steam_catalog",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("steam_price_alert",),
        push_category="alerts",
        url_name="steam_catalog:alert_rules",
        module="steam_catalog",
    ),
    NotificationGroup(
        key="investments",
        title="Инвестиции",
        description="Покупка вышла из трейд-бана и готова к продаже. Карточку покупки можно отправить в Telegram и вручную.",
        icon="steam_investments",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("steam_investment_unlocked",),
        push_category="investments",
        url_name="steam_trading:investments",
        module="steam_trading",
    ),
    NotificationGroup(
        key="farm",
        title="Ферма",
        description="Продажи дропа, изменения цен и бонусы на фарм-аккаунтах.",
        icon="farm_hub",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("farm_alert",),
        push_category="farm",
        url_name="farm:dashboard",
        module="farm",
    ),
    NotificationGroup(
        key="support",
        title="Поддержка",
        description="Ответ поддержки по вашему обращению и смена его статуса.",
        icon="support",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("support_reply", "support_status"),
        push_category="support",
        url_name="support:index",
        module="support",
    ),
)

# Notifications only staff receives; shown to admins only.
STAFF_GROUPS = (
    NotificationGroup(
        key="support_staff",
        title="Обращения в поддержку",
        description="Новое обращение и ответ пользователя в тикете — модераторам и выше.",
        icon="support_queue",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("support_new", "support_staff"),
        push_category="support",
        url_name="support:queue",
        module="support",
    ),
    NotificationGroup(
        key="antifraud",
        title="Мультиаккаунты",
        description="Антифрод нашёл связанные аккаунты — нужно решение администратора.",
        icon="cc_ban",
        channels=(SITE, PUSH),
        kinds=("antifraud_multi_account",),
        push_category="security",
    ),
    NotificationGroup(
        key="sponsors",
        title="Кандидаты в спонсоры",
        description="Донаты, по которым можно выдать статус спонсора.",
        icon="donations",
        channels=(SITE, PUSH),
        kinds=("donation_sponsor_candidates",),
    ),
    NotificationGroup(
        key="ops",
        title="Сбои сайта",
        description="Мониторинг заметил проблему: воркеры, очереди, внешние сервисы.",
        icon="warn",
        channels=(SITE, PUSH, TELEGRAM),
        kinds=("ops_alert",),
        url_name="control_center:health",
    ),
)


def _safe_reverse(url_name: str) -> str:
    if not url_name:
        return ""
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return ""


def _kind_stats(user, groups) -> dict[str, dict]:
    kinds = [kind for group in groups for kind in group.kinds]
    rows = (
        Notification.objects.filter(recipient=user, kind__in=kinds)
        .values("kind")
        .annotate(last=Max("created_at"), total=Count("id"))
    )
    return {row["kind"]: row for row in rows}


def build_group_rows(user, groups, *, module_states: dict[str, bool], disabled_push: set[str]) -> list[dict]:
    """Cards for the «what notifications exist» grid, with the user's push setting and latest activity."""
    groups = [group for group in groups if module_states.get(group.module, True)]
    stats = _kind_stats(user, groups)
    rows = []
    for group in groups:
        group_stats = [stats[kind] for kind in group.kinds if kind in stats]
        rows.append(
            {
                "group": group,
                "url": _safe_reverse(group.url_name),
                "channels": [{"key": key, "label": CHANNEL_LABELS[key]} for key in group.channels],
                "push_enabled": PUSH in group.channels and group.push_category not in disabled_push,
                "push_switchable": bool(group.push_category) and group.push_category in PUSH_CATEGORIES,
                "last_at": max((item["last"] for item in group_stats), default=None),
                "total": sum(item["total"] for item in group_stats),
            }
        )
    return rows


def site_summary(user) -> dict:
    month_ago = timezone.now() - timedelta(days=30)
    qs = Notification.objects.filter(recipient=user)
    return {
        "unread": qs.filter(is_read=False).count(),
        "month": qs.filter(created_at__gte=month_ago).count(),
    }
