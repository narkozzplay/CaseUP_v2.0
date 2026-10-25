"""Daily price digest for the public Telegram channel: the biggest risers and fallers of the last day.

Only items whose price was checked recently take part: a large part of the catalog is refreshed rarely,
and an old price would show a month of movement as one day. Links lead to the public SEO item pages.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

from django.db.models import OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory

from .models import TelegramNotifierConfig
from .services import TelegramNotifierError, ensure_public_base_url, get_telegram_notifier_config, send_message

WINDOW = timedelta(hours=24)
# The price "a day ago" is the last history point before the window; older than this it says nothing about the day.
MAX_BASELINE_AGE = timedelta(days=7)
MIN_CHANGE_PERCENT = Decimal("1")
# A slot missed by more than this (worker down, digest switched on late in the evening) is skipped.
SLOT_GRACE = timedelta(hours=2)
HASHTAGS = "#CaseUP #CounterStrike2"
# Telegram counts the text after markup is parsed: tags and link addresses are free.
TELEGRAM_TEXT_LIMIT = 4096
UTM = {"utm_source": "telegram", "utm_medium": "channel", "utm_campaign": "price_digest"}


class NothingToPost(TelegramNotifierError):
    pass


@dataclass(frozen=True)
class PriceMove:
    item: SteamItem
    baseline_price: Decimal
    current_price: Decimal
    change_percent: Decimal


@dataclass(frozen=True)
class PriceDigest:
    risers: list[PriceMove]
    fallers: list[PriceMove]
    generated_at: datetime

    @property
    def is_empty(self) -> bool:
        return not self.risers and not self.fallers


def parse_times(value: str) -> list[time]:
    """"20:00, 9:30" -> [09:30, 20:00]. Raises ValueError on a bad entry."""
    result = set()
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        hours, _, minutes = part.partition(":")
        result.add(time(int(hours), int(minutes or 0)))
    return sorted(result)


def collect_price_moves(*, config: TelegramNotifierConfig, now: datetime | None = None) -> PriceDigest:
    now = now or timezone.now()
    window_start = now - WINDOW
    min_price = config.channel_digest_min_price_rub or Decimal("0")
    top_n = max(1, min(int(config.channel_digest_top_n or 5), 10))

    baseline = (
        SteamItemPriceHistory.objects
        .filter(item=OuterRef("pk"), created_at__lt=window_start, created_at__gte=window_start - MAX_BASELINE_AGE)
        .order_by("-created_at")
        .values("price_rub")[:1]
    )
    # History is written only when the price changes, so items without a point inside the window did not move.
    moved_ids = SteamItemPriceHistory.objects.filter(created_at__gte=window_start, created_at__lte=now).values("item_id")
    items = (
        SteamItem.objects
        .filter(
            pk__in=moved_ids,
            is_active=True,
            price_rub__gte=min_price,
            price_checked_at__gte=now - timedelta(hours=int(config.channel_digest_fresh_hours or 12)),
        )
        .annotate(baseline_price=Subquery(baseline))
        .filter(baseline_price__gte=max(min_price, Decimal("0.01")))
    )

    moves = []
    for item in items:
        change = (item.price_rub - item.baseline_price) / item.baseline_price * 100
        change = change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if abs(change) >= MIN_CHANGE_PERCENT:
            moves.append(PriceMove(item, item.baseline_price, item.price_rub, change))

    def top(candidates):
        # One line per skin: five exteriors of the same weapon would crowd out everything else.
        picked, families = [], set()
        for move in candidates:
            family = move.item.family_name or move.item.market_hash_name
            if family in families:
                continue
            families.add(family)
            picked.append(move)
            if len(picked) == top_n:
                break
        return picked

    risers = top(sorted((m for m in moves if m.change_percent > 0), key=lambda m: m.change_percent, reverse=True))
    fallers = top(sorted((m for m in moves if m.change_percent < 0), key=lambda m: m.change_percent))
    return PriceDigest(risers=risers, fallers=fallers, generated_at=now)


def _price(value: Decimal) -> str:
    text = f"{value:,.2f}".replace(",", " ")
    return (text[:-3] if text.endswith(".00") else text) + " ₽"


def _link(base_url: str, path: str) -> str:
    return f"{base_url}{path}?{urlencode(UTM)}"


def render_digest(digest: PriceDigest, *, config: TelegramNotifierConfig) -> str:
    from modules.v2.system.services import get_site_name

    base_url = ensure_public_base_url(config)

    def lines(moves):
        result = []
        for index, move in enumerate(moves, start=1):
            name = html.escape(move.item.market_hash_name)
            url = html.escape(_link(base_url, move.item.get_absolute_url()), quote=True)
            sign = "+" if move.change_percent > 0 else ""
            result.append(
                f'{index}. <a href="{url}">{name}</a>\n'
                f"    {_price(move.baseline_price)} → <b>{_price(move.current_price)}</b> ({sign}{move.change_percent}%)"
            )
        return result

    parts = [f"📊 <b>Цены CS2 за сутки</b> · {timezone.localtime(digest.generated_at):%d.%m.%Y}"]
    if digest.risers:
        parts.append("📈 <b>Подорожали</b>\n" + "\n".join(lines(digest.risers)))
    if digest.fallers:
        parts.append("📉 <b>Подешевели</b>\n" + "\n".join(lines(digest.fallers)))
    catalog_url = html.escape(_link(base_url, reverse("steam_catalog:list")), quote=True)
    parts.append(
        f"Цены Steam Community Market, проверены за последние {config.channel_digest_fresh_hours} ч.\n"
        f'🔎 <a href="{catalog_url}">Все цены и графики — {html.escape(get_site_name())}</a>'
    )
    parts.append(HASHTAGS)
    return "\n\n".join(parts)


def build_digest_text(config: TelegramNotifierConfig | None = None, *, now: datetime | None = None) -> str | None:
    """The post text, or None when no fresh item moved enough to write about."""
    config = config or get_telegram_notifier_config()
    digest = collect_price_moves(config=config, now=now)
    if digest.is_empty:
        return None
    text = render_digest(digest, config=config)
    # Ten long sticker names in each list can outgrow one message: drop the tail rows until it fits.
    while visible_length(text) > TELEGRAM_TEXT_LIMIT and (len(digest.risers) > 1 or len(digest.fallers) > 1):
        longer = "risers" if len(digest.risers) >= len(digest.fallers) else "fallers"
        digest = replace(digest, **{longer: getattr(digest, longer)[:-1]})
        text = render_digest(digest, config=config)
    return text


def visible_length(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def due_slot(config: TelegramNotifierConfig, *, now: datetime | None = None) -> datetime | None:
    """Today's latest scheduled time that has come and not been posted yet."""
    now = now or timezone.now()
    try:
        times = parse_times(config.channel_digest_times)
    except ValueError:
        return None
    local_now = timezone.localtime(now)
    passed = [
        datetime.combine(local_now.date(), slot, tzinfo=local_now.tzinfo)
        for slot in times
    ]
    passed = [slot for slot in passed if slot <= local_now]
    if not passed:
        return None
    slot = passed[-1]
    if local_now - slot > SLOT_GRACE:
        return None
    last = config.channel_digest_last_sent_at
    if last is not None and last >= slot:
        return None
    return slot


def post_digest(config: TelegramNotifierConfig | None = None, *, now: datetime | None = None) -> str:
    """Sends the digest to the channel right away. Returns the posted text."""
    config = config or get_telegram_notifier_config()
    chat_id = (config.channel_chat_id or "").strip()
    if not chat_id:
        raise TelegramNotifierError("Канал не указан.")
    text = build_digest_text(config, now=now)
    if text is None:
        raise NothingToPost("За сутки нет заметных изменений среди предметов со свежими ценами.")
    send_message(text=text, chat_id=chat_id, parse_mode="HTML")
    return text


def post_due_digest(*, now: datetime | None = None) -> str:
    """Celery beat entry point: posts once per scheduled time. Returns what happened, for the task result."""
    now = now or timezone.now()
    config = get_telegram_notifier_config()
    if not config.channel_digest_enabled:
        return "disabled"
    if due_slot(config, now=now) is None:
        return "not_due"

    # Claim the slot first so two beat ticks (or two workers) never post the same digest twice.
    previous = config.channel_digest_last_sent_at
    claimed = TelegramNotifierConfig.objects.filter(
        pk=config.pk, channel_digest_last_sent_at=previous
    ).update(channel_digest_last_sent_at=now)
    if not claimed:
        return "already_claimed"

    try:
        post_digest(config, now=now)
    except TelegramNotifierError as exc:
        # Nothing to say today is not an error worth retrying; a Telegram failure is retried on the next tick.
        nothing_to_post = isinstance(exc, NothingToPost)
        TelegramNotifierConfig.objects.filter(pk=config.pk).update(
            channel_digest_last_error=f"{timezone.localtime(now):%d.%m %H:%M}: {exc}",
            **({} if nothing_to_post else {"channel_digest_last_sent_at": previous}),
        )
        return "empty" if nothing_to_post else "error"
    TelegramNotifierConfig.objects.filter(pk=config.pk).update(channel_digest_last_error="")
    return "posted"
