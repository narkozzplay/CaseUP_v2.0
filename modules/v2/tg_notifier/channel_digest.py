"""Daily price digest for the public Telegram channel, made of sections set up on the autopost page.

A section is e.g. "Ножи: рост" — the top N knives by price growth over the last day. Only items whose price was
checked recently take part: a large part of the catalog is refreshed rarely, and an old price would show a month
of movement as one day. Links lead to the public SEO item pages.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

from django.db.models import OuterRef, Q, Subquery
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory
from modules.v2.steam_catalog.services.browse import TYPE_LABELS, WEAPON_GROUPS, _weapon_group_q

from .models import ChannelDigestPost, ChannelDigestSection, TelegramNotifierConfig
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

CATEGORY_CHOICES = [
    ("all", "Все предметы"),
    *[(f"type:{key}", label) for key, label in TYPE_LABELS.items()],
    *[(f"group:{key}", label) for key, (label, _weapons) in WEAPON_GROUPS.items()],
]
CATEGORY_LABELS = dict(CATEGORY_CHOICES)


class NothingToPost(TelegramNotifierError):
    pass


@dataclass(frozen=True)
class PriceMove:
    item: SteamItem
    baseline_price: Decimal
    current_price: Decimal
    change_percent: Decimal


@dataclass
class DigestBlock:
    section: ChannelDigestSection
    moves: list[PriceMove] = field(default_factory=list)


@dataclass
class PriceDigest:
    blocks: list[DigestBlock]
    generated_at: datetime

    @property
    def is_empty(self) -> bool:
        return not any(block.moves for block in self.blocks)


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


def category_q(category: str) -> Q:
    kind, _, key = (category or "all").partition(":")
    if kind == "type" and key in TYPE_LABELS:
        return Q(item_type=key)
    if kind == "group":
        return _weapon_group_q(key) or Q()
    return Q()


def category_catalog_params(category: str) -> dict[str, str]:
    kind, _, key = (category or "all").partition(":")
    return {kind: key} if kind in ("type", "group") and key else {}


def _section_moves(section: ChannelDigestSection, *, config: TelegramNotifierConfig, now: datetime) -> list[PriceMove]:
    window_start = now - WINDOW
    min_price = max(section.min_price_rub or Decimal("0"), Decimal("0.01"))
    top_n = max(1, min(int(section.top_n or 5), 10))

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
        .filter(category_q(section.category))
        .filter(
            pk__in=moved_ids,
            is_active=True,
            price_rub__gte=min_price,
            price_checked_at__gte=now - timedelta(hours=int(config.channel_digest_fresh_hours or 12)),
        )
        .annotate(baseline_price=Subquery(baseline))
        .filter(baseline_price__gte=min_price)
    )

    rising = section.direction == ChannelDigestSection.Direction.UP
    moves = []
    for item in items:
        change = (item.price_rub - item.baseline_price) / item.baseline_price * 100
        change = change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if (change >= MIN_CHANGE_PERCENT) if rising else (change <= -MIN_CHANGE_PERCENT):
            moves.append(PriceMove(item, item.baseline_price, item.price_rub, change))
    moves.sort(key=lambda m: m.change_percent, reverse=rising)

    # One line per skin: five exteriors of the same weapon would crowd out everything else.
    picked, families = [], set()
    for move in moves:
        family = move.item.family_name or move.item.market_hash_name
        if family in families:
            continue
        families.add(family)
        picked.append(move)
        if len(picked) == top_n:
            break
    return picked


def collect_digest(*, config: TelegramNotifierConfig, now: datetime | None = None) -> PriceDigest:
    now = now or timezone.now()
    sections = ChannelDigestSection.objects.filter(is_enabled=True)
    return PriceDigest(
        blocks=[DigestBlock(section, _section_moves(section, config=config, now=now)) for section in sections],
        generated_at=now,
    )


def _price(value: Decimal) -> str:
    text = f"{value:,.2f}".replace(",", " ")
    return (text[:-3] if text.endswith(".00") else text) + " ₽"


def _link(base_url: str, path: str, params: dict[str, str] | None = None) -> str:
    return f"{base_url}{path}?{urlencode({**(params or {}), **UTM})}"


def render_digest(digest: PriceDigest, *, config: TelegramNotifierConfig) -> str:
    from modules.v2.system.services import get_site_name

    base_url = ensure_public_base_url(config)
    catalog_path = reverse("steam_catalog:list")

    def block_text(block: DigestBlock) -> str:
        section = block.section
        icon = "📈" if section.direction == ChannelDigestSection.Direction.UP else "📉"
        title = html.escape(section.title)
        params = category_catalog_params(section.category)
        if params:
            url = html.escape(_link(base_url, catalog_path, params), quote=True)
            title = f'<a href="{url}">{title}</a>'
        rows = [f"{icon} <b>{title}</b>"]
        for index, move in enumerate(block.moves, start=1):
            name = html.escape(move.item.market_hash_name)
            url = html.escape(_link(base_url, move.item.get_absolute_url()), quote=True)
            sign = "+" if move.change_percent > 0 else ""
            rows.append(
                f'{index}. <a href="{url}">{name}</a>\n'
                f"    {_price(move.baseline_price)} → <b>{_price(move.current_price)}</b> ({sign}{move.change_percent}%)"
            )
        return "\n".join(rows)

    parts = [f"📊 <b>Цены CS2 за сутки</b> · {timezone.localtime(digest.generated_at):%d.%m.%Y}"]
    parts.extend(block_text(block) for block in digest.blocks if block.moves)
    catalog_url = html.escape(_link(base_url, catalog_path), quote=True)
    parts.append(
        f"Цены Steam Community Market, проверены за последние {config.channel_digest_fresh_hours} ч.\n"
        f'🔎 <a href="{catalog_url}">Все цены и графики — {html.escape(get_site_name())}</a>'
    )
    parts.append(HASHTAGS)
    return "\n\n".join(parts)


def visible_length(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def build_digest_text(config: TelegramNotifierConfig | None = None, *, now: datetime | None = None) -> str | None:
    """The post text, or None when no fresh item moved enough to write about."""
    config = config or get_telegram_notifier_config()
    digest = collect_digest(config=config, now=now)
    if digest.is_empty:
        return None
    text = render_digest(digest, config=config)
    # Many sections with long sticker names can outgrow one message: drop tail rows of the longest block until it fits.
    while visible_length(text) > TELEGRAM_TEXT_LIMIT:
        longest = max(digest.blocks, key=lambda block: len(block.moves))
        if len(longest.moves) <= 1:
            break
        longest.moves = longest.moves[:-1]
        text = render_digest(digest, config=config)
    return text


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


def post_digest(
    config: TelegramNotifierConfig | None = None,
    *,
    now: datetime | None = None,
    trigger: str = ChannelDigestPost.Trigger.MANUAL,
) -> str:
    """Sends the digest to the channel right away and writes the attempt to the journal. Returns the posted text."""
    config = config or get_telegram_notifier_config()
    chat_id = (config.channel_chat_id or "").strip()
    text = ""
    try:
        if not chat_id:
            raise TelegramNotifierError("Канал не указан.")
        text = build_digest_text(config, now=now) or ""
        if not text:
            raise NothingToPost("За сутки нет заметных изменений среди предметов со свежими ценами.")
        send_message(text=text, chat_id=chat_id, parse_mode="HTML")
    except TelegramNotifierError as exc:
        status = ChannelDigestPost.Status.EMPTY if isinstance(exc, NothingToPost) else ChannelDigestPost.Status.ERROR
        ChannelDigestPost.objects.create(trigger=trigger, status=status, chat_id=chat_id, text=text, error=str(exc))
        raise
    ChannelDigestPost.objects.create(trigger=trigger, status=ChannelDigestPost.Status.SENT, chat_id=chat_id, text=text)
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
        post_digest(config, now=now, trigger=ChannelDigestPost.Trigger.SCHEDULE)
    except NothingToPost:
        # Nothing to say today is not worth retrying.
        return "empty"
    except TelegramNotifierError:
        # A Telegram failure is retried on the next tick.
        TelegramNotifierConfig.objects.filter(pk=config.pk).update(channel_digest_last_sent_at=previous)
        return "error"
    return "posted"
