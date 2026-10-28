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

from modules.v2.steam_catalog.models import SWEEP_SOURCE, SteamItem, SteamItemPriceHistory
from modules.v2.steam_catalog.services.browse import TYPE_LABELS, WEAPON_GROUPS, _weapon_group_q

from .models import ChannelDigestPost, ChannelDigestSection, TelegramNotifierConfig
from .services import (
    TelegramNotifierError,
    channel_utm_link,
    ensure_public_base_url,
    get_telegram_notifier_config,
    resolve_public_base_url,
    send_message,
    send_photo,
)

WINDOW = timedelta(hours=24)
# The price "a day ago" is the last history point before the window; older than this it says nothing about the day.
MAX_BASELINE_AGE = timedelta(days=7)
MIN_CHANGE_PERCENT = Decimal("1")
# A slot missed by more than this (worker down, digest switched on late in the evening) is skipped.
SLOT_GRACE = timedelta(hours=2)
# What the post wording on the autopost page may contain; anything else in braces is left as typed.
TEXT_PLACEHOLDERS = {
    "date": "дата поста, 25.10.2026",
    "time": "время поста, 21:00",
    "site": "название сайта",
    "hours": "сколько часов считается свежей цена",
}
# Telegram counts the text after markup is parsed: tags and link addresses are free.
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024

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

    @property
    def baseline_text(self) -> str:
        return _price(self.baseline_price)

    @property
    def current_text(self) -> str:
        return _price(self.current_price)

    @property
    def change_text(self) -> str:
        return f"{'+' if self.change_percent > 0 else ''}{self.change_percent}%"


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

    def copy(self) -> PriceDigest:
        return PriceDigest([DigestBlock(block.section, list(block.moves)) for block in self.blocks], self.generated_at)


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
        .exclude(source=SWEEP_SOURCE)
        .order_by("-created_at")
        .values("price_rub")[:1]
    )
    # History is written only when the price changes, so items without a point inside the window did not move.
    # The market sweep's lowest listing is not compared with the queue's median: its points are left out.
    moved_ids = (
        SteamItemPriceHistory.objects.filter(created_at__gte=window_start, created_at__lte=now)
        .exclude(source=SWEEP_SOURCE)
        .values("item_id")
    )
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


def _link(base_url: str, path: str, params: dict[str, str] | None = None, *, config: TelegramNotifierConfig, content: str) -> str:
    """A site link with UTM tags from the Telegram settings; utm_content says which link of the post it was."""
    query = f"?{urlencode(params)}" if params else ""
    return channel_utm_link(f"{base_url}{path}{query}", content=content, config=config)


def render_digest(digest: PriceDigest, *, config: TelegramNotifierConfig, compact: bool = False) -> str:
    """The post text. Compact (the caption under the picture) keeps one short line per item: the picture has the prices."""
    base_url = ensure_public_base_url(config)
    catalog_path = reverse("steam_catalog:list")

    def block_text(block: DigestBlock) -> str:
        section = block.section
        icon = "📈" if section.direction == ChannelDigestSection.Direction.UP else "📉"
        title = html.escape(section.title)
        params = category_catalog_params(section.category)
        if params:
            url = html.escape(_link(base_url, catalog_path, params, config=config, content="section"), quote=True)
            title = f'<a href="{url}">{title}</a>'
        rows = [f"{icon} <b>{title}</b>"]
        for index, move in enumerate(block.moves, start=1):
            name = html.escape(move.item.market_hash_name)
            url = html.escape(_link(base_url, move.item.get_absolute_url(), config=config, content="item"), quote=True)
            sign = "+" if move.change_percent > 0 else ""
            if compact:
                rows.append(f'{index}. <a href="{url}">{name}</a> <b>{sign}{move.change_percent}%</b>')
            else:
                rows.append(
                    f'{index}. <a href="{url}">{name}</a>\n'
                    f"    {_price(move.baseline_price)} → <b>{_price(move.current_price)}</b> ({sign}{move.change_percent}%)"
                )
        return "\n".join(rows)

    values = text_values(digest, config=config)
    title = config.channel_digest_title or TelegramNotifierConfig._meta.get_field("channel_digest_title").default
    parts = [f"<b>{fill_text(title, values)}</b>"]
    if intro := fill_text(config.channel_digest_intro, values):
        parts.append(intro)
    parts.extend(block_text(block) for block in digest.blocks if block.moves)
    if outro := fill_text(config.channel_digest_outro, values):
        parts.append(outro)
    link_text = fill_text(config.channel_digest_link_text, values)
    if compact:
        if link_text:
            top_url = html.escape(_link(base_url, reverse("price_movers"), config=config, content="top"), quote=True)
            parts.append(f'<a href="{top_url}">{link_text}</a>')
    else:
        footer = f"Цены Steam Community Market, проверены за последние {config.channel_digest_fresh_hours} ч."
        if link_text:
            catalog_url = html.escape(_link(base_url, catalog_path, config=config, content="catalog"), quote=True)
            footer += f'\n<a href="{catalog_url}">{link_text}</a>'
        parts.append(footer)
    if hashtags := fill_text(config.channel_digest_hashtags, values):
        parts.append(hashtags)
    return "\n\n".join(parts)


def text_values(digest: PriceDigest, *, config: TelegramNotifierConfig) -> dict[str, str]:
    from modules.v2.system.services import get_site_name

    local = timezone.localtime(digest.generated_at)
    return {
        "date": f"{local:%d.%m.%Y}",
        "time": f"{local:%H:%M}",
        "site": get_site_name(),
        "hours": str(config.channel_digest_fresh_hours),
    }


def fill_text(template: str, values: dict[str, str]) -> str:
    """The admin's wording with placeholders filled in, escaped for Telegram HTML. Unknown {names} stay as typed."""
    text = re.sub(r"\{(\w+)\}", lambda match: values.get(match.group(1), match.group(0)), (template or "").strip())
    return html.escape(text, quote=False)


def visible_length(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def fit_text(digest: PriceDigest, *, config: TelegramNotifierConfig, limit: int, compact: bool = False) -> str:
    """Renders a copy of the digest, dropping tail rows of the longest block until the text fits the limit."""
    digest = digest.copy()
    text = render_digest(digest, config=config, compact=compact)
    # Down to one row per block, the last blocks go away whole.
    while visible_length(text) > limit:
        filled = [block for block in digest.blocks if block.moves]
        if len(filled) <= 1 and len(filled[0].moves) <= 1:
            break
        longest = max(filled, key=lambda block: len(block.moves))
        if len(longest.moves) > 1:
            longest.moves = longest.moves[:-1]
        else:
            filled[-1].moves = []
        text = render_digest(digest, config=config, compact=compact)
    return text


def build_digest(config: TelegramNotifierConfig | None = None, *, now: datetime | None = None) -> PriceDigest | None:
    """Today's digest, or None when no fresh item moved enough to write about."""
    config = config or get_telegram_notifier_config()
    digest = collect_digest(config=config, now=now)
    return None if digest.is_empty else digest


def build_digest_text(config: TelegramNotifierConfig | None = None, *, now: datetime | None = None) -> str | None:
    """The post text, or None when no fresh item moved enough to write about."""
    config = config or get_telegram_notifier_config()
    digest = build_digest(config, now=now)
    return None if digest is None else fit_text(digest, config=config, limit=TELEGRAM_TEXT_LIMIT)


def build_preview_text(config: TelegramNotifierConfig) -> str | None:
    """What goes out as text: the caption under the picture, or the whole message without one."""
    digest = build_digest(config)
    if digest is None:
        return None
    if config.channel_digest_with_image:
        return fit_text(digest, config=config, limit=TELEGRAM_CAPTION_LIMIT, compact=True)
    return fit_text(digest, config=config, limit=TELEGRAM_TEXT_LIMIT)


def card_context(digest: PriceDigest, *, config: TelegramNotifierConfig) -> dict:
    """Context of tg_notifier/partials/digest_card.html (the picture and the public top page)."""
    from urllib.parse import urlparse

    from modules.v2.system.services import get_site_name

    base_url = (config.public_base_url or "").strip() or resolve_public_base_url(config)
    channel = (config.channel_chat_id or "").strip()
    return {
        "blocks": [block for block in digest.blocks if block.moves],
        "generated_at": digest.generated_at,
        "fresh_hours": config.channel_digest_fresh_hours,
        "title": config.channel_digest_card_title or TelegramNotifierConfig._meta.get_field("channel_digest_card_title").default,
        "site_name": get_site_name(),
        "site_host": urlparse(base_url).netloc or base_url,
        "channel_username": channel[1:] if channel.startswith("@") else "",
    }


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
    """Sends the digest to the channel right away and writes the attempt to the journal. Returns the posted text.

    With the picture switched on the post is a photo of the top with a compact caption. When the picture cannot be
    made or sent, the full text goes out as a plain message instead, so the post is never lost.
    """
    config = config or get_telegram_notifier_config()
    chat_id = (config.channel_chat_id or "").strip()
    text = ""
    note = ""
    try:
        if not chat_id:
            raise TelegramNotifierError("Канал не указан.")
        digest = build_digest(config, now=now)
        if digest is None:
            raise NothingToPost("За сутки нет заметных изменений среди предметов со свежими ценами.")
        sent = False
        if config.channel_digest_with_image:
            from .digest_image import DigestImageError, render_digest_image

            text = fit_text(digest, config=config, limit=TELEGRAM_CAPTION_LIMIT, compact=True)
            try:
                photo = render_digest_image(digest, config=config)
                send_photo(photo=photo, chat_id=chat_id, caption=text, parse_mode="HTML")
                sent = True
            except (DigestImageError, TelegramNotifierError) as exc:
                if "timeout" in str(exc).lower() and not isinstance(exc, DigestImageError):
                    raise  # Telegram may have the photo already: a text copy would post the digest twice.
                note = f"Без картинки: {exc}"
        if not sent:
            text = fit_text(digest, config=config, limit=TELEGRAM_TEXT_LIMIT)
            send_message(text=text, chat_id=chat_id, parse_mode="HTML")
    except TelegramNotifierError as exc:
        status = ChannelDigestPost.Status.EMPTY if isinstance(exc, NothingToPost) else ChannelDigestPost.Status.ERROR
        error = "\n".join(part for part in (note, str(exc)) if part)
        ChannelDigestPost.objects.create(trigger=trigger, status=status, chat_id=chat_id, text=text, error=error)
        raise
    ChannelDigestPost.objects.create(
        trigger=trigger, status=ChannelDigestPost.Status.SENT, chat_id=chat_id, text=text, error=note
    )
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
