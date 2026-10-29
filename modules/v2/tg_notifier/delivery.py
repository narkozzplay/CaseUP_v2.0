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

# Site notification kinds copied to Telegram: kind -> (urgent, button label).
MIRROR_ICONS = {"market_deal": "🤝", "market_payout": "💸", "market_listing_missing": "⚠️", "daily_bonus_reminder": "🎁"}
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
        f"⏸ <b>Дневной лимит исчерпан</b> · {prefs.daily_limit} в день\n\n"
        "<blockquote>До полуночи алерты будут только на сайте, в колокольчике.\n"
        "Сделки рынка по-прежнему приходят сюда.</blockquote>"
    )
    return f"{text}\n\n⚙️ <a href=\"{html.escape(link, quote=True)}\">Изменить лимит</a>" if link else text


def deliver_notice(
    user,
    text: str,
    *,
    urgent: bool = False,
    reply_markup: dict[str, Any] | None = None,
    now: datetime | None = None,
    notification=None,
    expires_at: datetime | None = None,
) -> str:
    """Sends an HTML notice to the user's linked chat, following their quiet hours and daily cap.

    Returns SENT, DEFERRED (held for the end of quiet hours), LIMITED (cap reached) or UNLINKED (no chat).
    A deferred notice is dropped unsent once `notification` is read or `expires_at` passes.
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
        TelegramDeferredNotice.objects.create(
            user=user, text=text, reply_markup=reply_markup or {}, notification=notification, expires_at=expires_at
        )
        return DEFERRED
    allowed, note = _take_slot(user, now=now)
    if not allowed:
        if note:
            try:
                send_message(text=_limit_note(prefs), chat_id=chat_id, parse_mode=HTML)
            except TelegramNotifierError:
                logger.warning("tg_notifier: limit note to user %s failed", user.pk, exc_info=True)
        return LIMITED
    try:
        send_message(text=text, chat_id=chat_id, parse_mode=HTML, reply_markup=reply_markup)
    except TelegramNotifierError:
        _reserve(user, -1, now=now)  # not delivered, so it does not count against the cap
        raise
    return SENT


def _reserve(user, wanted: int, *, now: datetime) -> int:
    """Takes up to `wanted` slots of today's cap without the «cap reached» note (a negative number gives slots
    back); returns how many were taken."""
    today = timezone.localdate(now)
    with transaction.atomic():
        prefs = TelegramUserPrefs.objects.select_for_update().get(user=user)
        if prefs.counter_date != today:
            prefs.counter_date, prefs.counter_value = today, 0
        if wanted < 0:  # giving slots back
            granted = -min(-wanted, prefs.counter_value)
        else:
            granted = wanted if not prefs.daily_limit else max(0, min(wanted, prefs.daily_limit - prefs.counter_value))
        prefs.counter_value += granted
        prefs.save(update_fields=["counter_date", "counter_value", "updated_at"])
    return granted


def flush_deferred(*, now: datetime | None = None) -> int:
    """Sends what quiet hours held back to users whose quiet hours are over: each notice with its buttons (only
    the first one with sound), within the daily cap; the rest stay on the site. Returns users served."""
    now = now or timezone.now()
    config = get_telegram_notifier_config()
    if not is_enabled(config):
        return 0
    served = 0
    user_ids = TelegramDeferredNotice.objects.values_list("user_id", flat=True).distinct()
    for prefs in TelegramUserPrefs.objects.filter(user_id__in=list(user_ids)).select_related("user"):
        if in_quiet_hours(prefs, now):
            continue
        queued = TelegramDeferredNotice.objects.filter(user_id=prefs.user_id)
        # Stale ones go unsent: past their deadline, or their site notification is already dealt with.
        queued.filter(Q(expires_at__lte=now) | Q(notification__is_read=True)).delete()
        notices = list(queued)
        if not notices:
            continue
        chat_id = chat_id_of(prefs.user)
        if not chat_id:
            TelegramDeferredNotice.objects.filter(pk__in=[notice.pk for notice in notices]).delete()
            continue
        granted = _reserve(prefs.user, len(notices), now=now)
        done, sent = [], 0
        try:
            for index, notice in enumerate(notices[:granted]):
                send_message(text=notice.text, chat_id=chat_id, parse_mode=HTML, reply_markup=notice.reply_markup or None, disable_notification=index > 0)
                done.append(notice.pk)
                sent += 1
            if granted < len(notices):
                done += [notice.pk for notice in notices[granted:]]
                send_message(
                    text=f"🌙 <b>Ещё {len(notices) - granted}</b> уведомл. за ночь не вошли в дневной лимит — <i>они есть на сайте в колокольчике.</i>",
                    chat_id=chat_id, parse_mode=HTML, disable_notification=True,
                )
        except TelegramNotifierError:
            logger.warning("tg_notifier: deferred notices to user %s failed", prefs.user_id, exc_info=True)
            if sent < granted:
                # Unsent notices stay queued for the next tick and get their slots back; the over-cap ones are
                # left queued too, so the next tick decides about them again.
                _reserve(prefs.user, -(granted - sent), now=now)
                done = done[:sent]
        TelegramDeferredNotice.objects.filter(pk__in=done).delete()
        if done:
            served += 1
    return served


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
    expires_at = None
    if notification.kind == "daily_bonus_reminder":  # the bonus has to be claimed before midnight
        local = timezone.localtime(notification.created_at)
        expires_at = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    view = render_notification(notification, "ru")
    text = f"{MIRROR_ICONS.get(notification.kind, '🔔')} <b>{html.escape(view.title)}</b>\n\n<blockquote>{html.escape(view.body)}</blockquote>"
    markup = None
    if view.url:
        try:
            markup = {"inline_keyboard": [[url_button(button, site_link(view.url, content=notification.kind))]]}
        except TelegramNotifierError:
            markup = None
    return deliver_notice(
        notification.recipient, text, urgent=urgent, reply_markup=markup, notification=notification, expires_at=expires_at
    )


def on_notification_saved(sender, instance, created, update_fields=None, **kwargs) -> None:
    """post_save hook: new notifications of mirrored kinds, and old ones brought back to the top, go to Telegram."""
    if instance.kind not in MIRRORED_KINDS:
        return
    if not created and not (update_fields and "created_at" in update_fields):
        return
    from .tasks import mirror_notification_task

    notification_id = instance.pk

    def enqueue() -> None:
        # Runs after the caller's transaction has committed: a broker outage must not turn their request into a
        # 500, so the copy is sent inline instead.
        try:
            mirror_notification_task.delay(notification_id)
        except Exception:  # noqa: BLE001
            logger.warning("tg_notifier: queueing mirror of notification %s failed, sending inline", notification_id, exc_info=True)
            try:
                mirror_notification(notification_id)
            except Exception:  # noqa: BLE001
                logger.warning("tg_notifier: inline mirror of notification %s failed", notification_id, exc_info=True)

    transaction.on_commit(enqueue)


# --------------------------------------------------------------------------- personal digest


def digest_due(prefs: TelegramUserPrefs, now: datetime) -> bool:
    local = timezone.localtime(now)
    if not prefs.digest_enabled or prefs.digest_last_sent_on == local.date():
        return False
    return local.time() >= prefs.digest_time


def build_personal_digest(user, *, now: datetime | None = None) -> str | None:
    """Favorites moved over the day, the portfolio total and the day's alerts; None when there is nothing to say."""
    from modules.v2.steam_catalog.models import SteamItemFavorite, UserPriceAlertEvent

    from .bot import change_since, item_row, portfolio_summary, quote

    now = now or timezone.now()
    lines = [f"☀️ <b>Сводка CaseUP</b>\n<i>{timezone.localtime(now):%d.%m} · что изменилось за сутки</i>"]
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
        lines.append("\n⭐ <b>Избранное</b>")
        rows = [item_row(index, item.display_name, item.price_rub, change[1]) for index, (_abs, item, change) in enumerate(moves[:10], start=1)]
        if len(moves) > 10:
            rows.append(f"<i>…и ещё {len(moves) - 10}</i>")
        lines.append(quote(rows))
    elif favorites:
        lines.append("\n⭐ <i>Цены в избранном за сутки не изменились.</i>")

    portfolio = portfolio_summary(user)
    if portfolio is not None:
        has_content = True
        lines.append("\n💼 <b>Портфель</b>")
        lines.append(portfolio)

    alerts = UserPriceAlertEvent.objects.filter(user=user, created_at__gte=now - timedelta(days=1)).count()
    if alerts:
        has_content = True
        lines.append(f"\n🔔 Сработало алертов за сутки: <b>{alerts}</b>")

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
        text = build_personal_digest(prefs.user, now=now)
        if text:
            from .bot import main_keyboard

            try:
                send_message(text=text, chat_id=chat_id_of(prefs.user), parse_mode=HTML, reply_markup=main_keyboard())
            except TelegramNotifierError:
                # Not marked as sent: the next tick tries again.
                logger.warning("tg_notifier: digest to user %s failed", prefs.user_id, exc_info=True)
                continue
            sent += 1
        TelegramUserPrefs.objects.filter(pk=prefs.pk).update(digest_last_sent_on=timezone.localdate(now))
    return sent

