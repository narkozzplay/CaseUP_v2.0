"""Personal bot notices: quiet hours, the daily cap, Telegram copies of site notifications and the personal digest.

Two kinds of notices:
- urgent (market deals and payouts): they have deadlines, so they go out at once — silently during quiet hours —
  and never count towards the daily cap;
- regular (price alerts, the daily bonus): held during quiet hours and sent as one message when they end; each one
  takes a slot of the daily cap, and once the cap is used up they stay on the site only until midnight.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile

from .models import TelegramDeferredNotice, TelegramUserPrefs
from .services import (
    TelegramNotifierError,
    bot_utm_link,
    ensure_public_base_url,
    get_telegram_notifier_config,
    is_enabled,
    send_message,
)

logger = logging.getLogger(__name__)

HTML = "HTML"
SENT, DEFERRED, LIMITED, UNLINKED = "sent", "deferred", "limited", "unlinked"
# Telegram cuts messages at 4096 characters.
MESSAGE_LIMIT = 4000

# Site notification kinds copied to Telegram: kind -> (urgent, button label).
MIRRORED_KINDS = {
    "market_deal": (True, "Открыть сделку"),
    "market_payout": (True, "Открыть выплаты"),
    "market_listing_missing": (True, "Открыть мои лоты"),
    "daily_bonus_reminder": (False, "Забрать на сайте"),
}


def get_prefs(user) -> TelegramUserPrefs:
    prefs, _ = TelegramUserPrefs.objects.get_or_create(user=user)
    return prefs


def in_quiet_hours(prefs: TelegramUserPrefs, now: datetime | None = None) -> bool:
    if not prefs.quiet_enabled or prefs.quiet_from == prefs.quiet_to:
        return False
    moment = timezone.localtime(now or timezone.now()).time()
    if prefs.quiet_from < prefs.quiet_to:
        return prefs.quiet_from <= moment < prefs.quiet_to
    return moment >= prefs.quiet_from or moment < prefs.quiet_to


def site_link(path: str, *, content: str) -> str:
    """An absolute site link for a bot message, with the bot's UTM tags."""
    return bot_utm_link(f"{ensure_public_base_url()}{path}", content=content)


def url_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def chat_id_of(user) -> str:
    profile = UserProfile.objects.filter(user=user).only("telegram_chat_id").first()
    return (getattr(profile, "telegram_chat_id", "") or "").strip()


def _take_slot(user, *, now: datetime) -> tuple[bool, bool]:
    """Counts one regular notice against the daily cap. Returns (allowed, send the «cap reached» note now)."""
    today = timezone.localdate(now)
    with transaction.atomic():
        prefs = TelegramUserPrefs.objects.select_for_update().get(user=user)
        if prefs.counter_date != today:
            prefs.counter_date, prefs.counter_value = today, 0
        if prefs.daily_limit and prefs.counter_value >= prefs.daily_limit:
            first_refusal = prefs.limit_notice_on != today
            prefs.limit_notice_on = today
            prefs.save(update_fields=["counter_date", "counter_value", "limit_notice_on", "updated_at"])
            return False, first_refusal
        prefs.counter_value += 1
        prefs.save(update_fields=["counter_date", "counter_value", "updated_at"])
        return True, False


def _limit_note(prefs: TelegramUserPrefs) -> str:
    try:
        link = site_link(reverse("profiles:profile_settings") + "#settings-telegram", content="limit")
    except TelegramNotifierError:
        link = ""
    text = (
        f"⏸ На сегодня пришло {prefs.daily_limit} уведомлений — это ваш дневной лимит. "
        "До полуночи алерты будут только на сайте, сделки рынка по-прежнему приходят сюда."
    )
    return f"{text}\n\nИзменить лимит: {link}" if link else text


def deliver_notice(
    user,
    text: str,
    *,
    urgent: bool = False,
    reply_markup: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Sends an HTML notice to the user's linked chat, following their quiet hours and daily cap.

    Returns SENT, DEFERRED (held for the end of quiet hours), LIMITED (cap reached) or UNLINKED (no chat).
    Raises TelegramNotifierError when Telegram refuses the message.
    """
    now = now or timezone.now()
    chat_id = chat_id_of(user)
    if not chat_id:
        return UNLINKED
    prefs = get_prefs(user)
    quiet = in_quiet_hours(prefs, now)
    if urgent:
        send_message(text=text, chat_id=chat_id, parse_mode=HTML, reply_markup=reply_markup, disable_notification=quiet)
        return SENT
    if quiet:
        TelegramDeferredNotice.objects.create(user=user, text=text)
        return DEFERRED
    allowed, note = _take_slot(user, now=now)
    if not allowed:
        if note:
            try:
                send_message(text=_limit_note(prefs), chat_id=chat_id)
            except TelegramNotifierError:
                logger.warning("tg_notifier: limit note to user %s failed", user.pk, exc_info=True)
        return LIMITED
    send_message(text=text, chat_id=chat_id, parse_mode=HTML, reply_markup=reply_markup)
    return SENT


def flush_deferred(*, now: datetime | None = None) -> int:
    """Sends what quiet hours held back, one message per user whose quiet hours are over. Returns users served."""
    now = now or timezone.now()
    config = get_telegram_notifier_config()
    if not is_enabled(config):
        return 0
    served = 0
    user_ids = TelegramDeferredNotice.objects.values_list("user_id", flat=True).distinct()
    for prefs in TelegramUserPrefs.objects.filter(user_id__in=list(user_ids)).select_related("user"):
        if in_quiet_hours(prefs, now):
            continue
        notices = list(TelegramDeferredNotice.objects.filter(user_id=prefs.user_id))
        chat_id = chat_id_of(prefs.user)
        if chat_id:
            try:
                send_message(text=_join_deferred(notices), chat_id=chat_id, parse_mode=HTML)
            except TelegramNotifierError:
                logger.warning("tg_notifier: deferred notices to user %s failed", prefs.user_id, exc_info=True)
                continue
            served += 1
        TelegramDeferredNotice.objects.filter(pk__in=[notice.pk for notice in notices]).delete()
    return served


def _join_deferred(notices: list[TelegramDeferredNotice]) -> str:
    head = f"🌙 Пока шли тихие часы, пришло уведомлений: {len(notices)}"
    parts, length = [head], len(head)
    for index, notice in enumerate(notices):
        if length + len(notice.text) + 2 > MESSAGE_LIMIT - 80:
            parts.append(f"…и ещё {len(notices) - index} — они есть на сайте в колокольчике.")
            break
        parts.append(notice.text)
        length += len(notice.text) + 2
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- site notifications


def mirror_notification(notification_id: int) -> str:
    """The Telegram copy of a site notification of a MIRRORED_KINDS kind."""
    from modules.v2.notifications.models import Notification
    from modules.v2.notifications.services import render_notification

    notification = Notification.objects.select_related("recipient").filter(pk=notification_id).first()
    if notification is None or notification.kind not in MIRRORED_KINDS:
        return "skipped"
    if not is_enabled():
        return "disabled"
    urgent, button = MIRRORED_KINDS[notification.kind]
    view = render_notification(notification, "ru")
    text = f"<b>{html.escape(view.title)}</b>\n{html.escape(view.body)}"
    markup = None
    if view.url:
        try:
            markup = {"inline_keyboard": [[url_button(button, site_link(view.url, content=notification.kind))]]}
        except TelegramNotifierError:
            markup = None
    return deliver_notice(notification.recipient, text, urgent=urgent, reply_markup=markup)


def on_notification_saved(sender, instance, created, update_fields=None, **kwargs) -> None:
    """post_save hook: new notifications of mirrored kinds, and old ones brought back to the top, go to Telegram."""
    if instance.kind not in MIRRORED_KINDS:
        return
    if not created and not (update_fields and "created_at" in update_fields):
        return
    from .tasks import mirror_notification_task

    notification_id = instance.pk
    transaction.on_commit(lambda: mirror_notification_task.delay(notification_id))


# --------------------------------------------------------------------------- personal digest


def digest_due(prefs: TelegramUserPrefs, now: datetime) -> bool:
    local = timezone.localtime(now)
    if not prefs.digest_enabled or prefs.digest_last_sent_on == local.date():
        return False
    return local.time() >= prefs.digest_time


def build_personal_digest(user, *, now: datetime | None = None) -> str | None:
    """Favorites moved over the day, the portfolio total and the day's alerts; None when there is nothing to say."""
    from modules.v2.steam_catalog.models import SteamItemFavorite, UserPriceAlertEvent

    from .bot import change_since, fmt_change, fmt_price, portfolio_summary

    now = now or timezone.now()
    lines = [f"☀️ <b>Сводка CaseUP · {timezone.localtime(now):%d.%m}</b>"]
    has_content = False

    favorites = list(SteamItemFavorite.objects.filter(user=user).select_related("item")[:50])
    moves = []
    for favorite in favorites:
        item = favorite.item
        change = change_since(item, timedelta(days=1), now=now)
        if change is not None and change[1] != 0:
            moves.append((abs(change[1]), item, change))
    moves.sort(key=lambda row: row[0], reverse=True)
    if moves:
        has_content = True
        lines.append("\n<b>Избранное за сутки</b>")
        for _abs, item, change in moves[:10]:
            lines.append(f"• {html.escape(item.display_name)} — {fmt_price(item.price_rub)} ({fmt_change(change)})")
        if len(moves) > 10:
            lines.append(f"…и ещё {len(moves) - 10}")
    elif favorites:
        lines.append("\nЦены в избранном за сутки не изменились.")

    portfolio = portfolio_summary(user)
    if portfolio is not None:
        has_content = True
        lines.append("\n<b>Портфель</b>")
        lines.append(portfolio)

    alerts = UserPriceAlertEvent.objects.filter(user=user, created_at__gte=now - timedelta(days=1)).count()
    if alerts:
        has_content = True
        lines.append(f"\n🔔 Сработало алертов за сутки: {alerts}")

    return "\n".join(lines) if has_content else None


def send_due_digests(*, now: datetime | None = None) -> int:
    now = now or timezone.now()
    if not is_enabled():
        return 0
    sent = 0
    linked = UserProfile.objects.exclude(Q(telegram_chat_id__isnull=True) | Q(telegram_chat_id="")).values("user_id")
    for prefs in TelegramUserPrefs.objects.filter(digest_enabled=True, user_id__in=linked).select_related("user"):
        if not digest_due(prefs, now):
            continue
        TelegramUserPrefs.objects.filter(pk=prefs.pk).update(digest_last_sent_on=timezone.localdate(now))
        text = build_personal_digest(prefs.user, now=now)
        if not text:
            continue
        from .bot import main_keyboard

        try:
            send_message(text=text, chat_id=chat_id_of(prefs.user), parse_mode=HTML, reply_markup=main_keyboard())
        except TelegramNotifierError:
            logger.warning("tg_notifier: digest to user %s failed", prefs.user_id, exc_info=True)
            continue
        sent += 1
    return sent

