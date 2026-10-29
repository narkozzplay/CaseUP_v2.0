"""Incoming bot updates: Start with a link token, commands and button presses (answered in bot.py).

Telegram reaches the site either by webhook (Telegram -> relay -> site) or, when incoming connections to the
relay hosting are blocked, by polling: the site itself asks Telegram for new updates through the relay —
the same outbound path test messages use. Both paths end in handle_update().
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from .models import TelegramNotifierConfig
from .services import (
    BOT_UPDATE_TYPES,
    TelegramNotifierError,
    _build_telegram_endpoint,
    _telegram_api_request,
    bind_profile_by_telegram_start,
    extract_start_token_from_message_text,
    get_telegram_notifier_config,
    is_enabled,
    resolve_bot_token,
    send_message,
    touch_profile_last_interaction_by_chat_id,
)

logger = logging.getLogger(__name__)

# The relay cuts upstream calls at 20 s, so the long poll has to finish well before that.
LONG_POLL_SECONDS = 10
# Older messages are dropped instead of answered: link tokens live 30 minutes anyway.
STALE_UPDATE_SECONDS = 30 * 60
POLL_STATUS_KEY = "tg_notifier:poll_status"
# Polls run every few seconds (at most 2 minutes apart while backing off): a longer gap means the poller was down.
POLL_GAP_MINUTES = 5


def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    from .bot import handle_callback, handle_message, main_keyboard, welcome_reply

    if payload.get("callback_query"):
        return handle_callback(payload["callback_query"])

    message = payload.get("message") or {}
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}

    chat_id = str(chat.get("id") or "").strip()
    text = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True, "ignored": "no_chat_id"}

    start_token = extract_start_token_from_message_text(text)
    if start_token and chat.get("type", "private") != "private":
        # Personal notices (deals, payouts) must never land in a group the link was posted to.
        return {"ok": True, "ignored": "not_private"}
    if start_token:
        try:
            bind_profile_by_telegram_start(
                start_token=start_token,
                chat_id=chat_id,
                telegram_username=str(from_user.get("username") or "").strip(),
                telegram_first_name=str(from_user.get("first_name") or "").strip(),
            )
        except TelegramNotifierError as exc:
            try:
                send_message(
                    chat_id=chat_id,
                    text=f"❌ Не удалось подключить Telegram к профилю CaseUP: {exc}",
                )
            except TelegramNotifierError:
                pass
            return {"ok": True, "bound": False, "error": str(exc)}

        warning = ""
        welcome = welcome_reply()
        try:
            send_message(chat_id=chat_id, text=welcome.text, reply_markup=main_keyboard())
        except TelegramNotifierError as exc:
            warning = str(exc)

        if warning:
            return {"ok": True, "bound": True, "warning": warning}
        return {"ok": True, "bound": True}

    touch_profile_last_interaction_by_chat_id(chat_id)
    if not text:
        return {"ok": True, "ignored": "no_text"}
    return handle_message(message)


def _save_status(*, error: str, handled: int) -> None:
    """Besides the last poll, keeps when the current run of failures began (failing_since) and when the current run
    of clean polls began (ok_since): the watchdog alerts only on sustained trouble, not on a single 502, and reports
    recovery only after a sustained clean run."""
    now = timezone.now()
    previous = cache.get(POLL_STATUS_KEY) or {}
    # A gap without polls (poller stopped) breaks a streak just like an error does.
    continuous = bool(previous) and (now - previous["at"]) < timedelta(minutes=POLL_GAP_MINUTES)
    status = {"at": now, "error": error, "handled": handled, "failing_since": None, "ok_since": None}
    if error:
        status["failing_since"] = (previous.get("failing_since") if continuous and previous.get("error") else None) or now
    else:
        status["ok_since"] = (previous.get("ok_since") if continuous and not previous.get("error") else None) or now
    cache.set(POLL_STATUS_KEY, status, timeout=7 * 24 * 3600)


def get_poll_status() -> dict[str, Any] | None:
    """Last poll as {at, error, handled, failing_since, ok_since}; None if the poller never ran."""
    return cache.get(POLL_STATUS_KEY)


def poll_updates(config: TelegramNotifierConfig | None = None, *, long_poll: int = LONG_POLL_SECONDS) -> int:
    """Fetches and handles pending updates once. Returns how many were handled; raises TelegramNotifierError."""
    config = config or get_telegram_notifier_config()
    bot_token = resolve_bot_token(config)
    if not bot_token:
        raise TelegramNotifierError("Bot token не настроен.")

    payload = {"offset": config.polling_offset + 1, "timeout": long_poll, "allowed_updates": BOT_UPDATE_TYPES}
    try:
        data = _telegram_api_request(
            endpoint=_build_telegram_endpoint(bot_token, "getUpdates"),
            payload=payload,
            timeout=long_poll + 15,
        )
    except TelegramNotifierError as exc:
        # 409: a webhook is still registered; Telegram refuses getUpdates until it is removed.
        if "409" in str(exc) or "webhook is active" in str(exc):
            _telegram_api_request(
                endpoint=_build_telegram_endpoint(bot_token, "deleteWebhook"),
                payload={"drop_pending_updates": False},
            )
            return 0
        raise

    handled = 0
    now = time.time()
    for update in data.get("result") or []:
        update_id = int(update.get("update_id") or 0)
        # Button presses carry the date of the message they sit under, which may be old: only messages go stale.
        message_date = int((update.get("message") or {}).get("date") or 0)
        if message_date and now - message_date > STALE_UPDATE_SECONDS:
            logger.info("tg_notifier: skipped stale update %s", update_id)
        else:
            try:
                handle_update(update)
                handled += 1
            except Exception:  # one broken update must not block the queue
                logger.exception("tg_notifier: update %s failed", update_id)
        if update_id > config.polling_offset:
            config.polling_offset = update_id
            TelegramNotifierConfig.objects.filter(pk=config.pk).update(polling_offset=update_id)
    return handled


def poll_once() -> tuple[bool, int]:
    """One poller tick. Returns (worked, handled): worked is False when polling is off and the caller should idle."""
    config = get_telegram_notifier_config()
    if config.updates_mode != TelegramNotifierConfig.UpdatesMode.POLLING or not is_enabled(config) or not resolve_bot_token(config):
        return False, 0
    from .bot import ensure_commands

    ensure_commands()
    try:
        handled = poll_updates(config)
    except TelegramNotifierError as exc:
        _save_status(error=str(exc)[:300], handled=0)
        raise
    _save_status(error="", handled=handled)
    return True, handled
