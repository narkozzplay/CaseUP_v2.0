"""The weekly portfolio summary in the bot: on Mondays, at the user's digest time, a chart picture and the week.

Only for users with the investments analytics plan feature who have any purchase. The picture is drawn with Pillow
(numbers and dates only, so the built-in font is enough); everything in words goes to the caption.
"""
from __future__ import annotations

import html
import io
import logging
from datetime import datetime
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile

from .delivery import HTML, chat_id_of, url_button
from .models import TelegramUserPrefs
from .services import TelegramNotifierError, is_enabled, send_message, send_photo

logger = logging.getLogger(__name__)

MONDAY = 0
WIDTH, HEIGHT = 1200, 600
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 40, 150, 40, 70
BACKGROUND = (12, 15, 20)
GRID = (40, 44, 52)
VALUE = (245, 166, 35)
INVESTED = (170, 174, 182)
LABEL = (150, 155, 165)
UP, DOWN = (61, 220, 132), (255, 93, 93)
# Telegram cuts photo captions at 1024 characters.
CAPTION_LIMIT = 1024


def weekly_due(prefs: TelegramUserPrefs, now: datetime) -> bool:
    local = timezone.localtime(now)
    if not prefs.weekly_portfolio_enabled or local.weekday() != MONDAY:
        return False
    if prefs.weekly_portfolio_last_sent_on == local.date():
        return False
    return local.time() >= prefs.digest_time


def _has_access(user) -> bool:
    from modules.v2.subscriptions.feature_codes import STEAM_TRADING_ADVANCED_ANALYTICS
    from modules.v2.subscriptions.services import get_feature_access_state

    return get_feature_access_state(user, STEAM_TRADING_ADVANCED_ANALYTICS).is_available


def _label(value: Decimal) -> str:
    return f"{value:,.0f}".replace(",", " ")


def render_week_chart(days) -> bytes:
    """A PNG of the value line over the dashed cost line, with the last value tagged on the right."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    big = ImageFont.load_default(size=28)

    values = [float(day.value_rub) for day in days] + [float(day.invested_rub) for day in days]
    low, high = min(values), max(values)
    pad = (high - low) * 0.15 or max(high * 0.05, 1.0)
    low, high = max(low - pad, 0.0), high + pad
    plot_w, plot_h = WIDTH - PAD_LEFT - PAD_RIGHT, HEIGHT - PAD_TOP - PAD_BOTTOM

    def xy(index: int, value) -> tuple[float, float]:
        x = PAD_LEFT + (plot_w * index / (len(days) - 1) if len(days) > 1 else plot_w / 2)
        y = PAD_TOP + plot_h * (1 - (float(value) - low) / (high - low))
        return x, y

    for step in range(5):
        y = PAD_TOP + plot_h * step / 4
        draw.line([(PAD_LEFT, y), (PAD_LEFT + plot_w, y)], fill=GRID, width=1)
        draw.text((PAD_LEFT + plot_w + 14, y), _label(Decimal(high - (high - low) * step / 4)), fill=LABEL, font=font, anchor="lm")

    invested = [xy(i, day.invested_rub) for i, day in enumerate(days)]
    for start, end in zip(invested, invested[1:]):
        # Dashed: every other short segment of the line.
        length = max(int(((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5 // 12), 1)
        for part in range(0, length, 2):
            a, b = part / length, min((part + 1) / length, 1)
            draw.line([(start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a),
                       (start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b)], fill=INVESTED, width=3)
    value = [xy(i, day.value_rub) for i, day in enumerate(days)]
    if len(value) > 1:
        draw.line(value, fill=VALUE, width=6, joint="curve")
    last_x, last_y = value[-1]
    draw.ellipse([last_x - 9, last_y - 9, last_x + 9, last_y + 9], fill=VALUE)

    for index, day in enumerate(days):
        x, _y = xy(index, 0)
        draw.text((x, HEIGHT - PAD_BOTTOM + 30), day.day.strftime("%d.%m"), fill=LABEL, font=font, anchor="mm")

    last = days[-1]
    pnl = last.value_rub - last.invested_rub
    draw.text((PAD_LEFT + plot_w + 14, last_y), _label(last.value_rub), fill=VALUE, font=big, anchor="lm")
    draw.text((PAD_LEFT, 14), "P/L " + ("+" if pnl > 0 else "") + _label(pnl), fill=UP if pnl >= 0 else DOWN, font=big, anchor="lt")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_weekly_caption(summary, analytics, *, movers: int = 3) -> str:
    from .bot import fmt_percent, fmt_price, fmt_signed_price, quote, trend

    lines = [f"💼 <b>Портфель за неделю</b>\n<i>{summary.start:%d.%m}–{summary.end:%d.%m}</i>", ""]
    total = [f"📦 Стоит сейчас: <b>{fmt_price(summary.value_rub)}</b>"]
    change = f"{trend(summary.change_rub)} За неделю: <b>{fmt_signed_price(summary.change_rub)}</b>"
    if summary.change_percent is not None:
        change += f" ({fmt_percent(summary.change_percent)})"
    total.append(change)
    lines.append(quote(total))

    def mover(row) -> str:
        return f"{fmt_signed_price(row.change_rub)}  {html.escape(row.name)} ×{row.quantity}"

    if summary.gainers[:movers]:
        lines += ["", "🏆 <b>Подняли портфель</b>", quote([mover(row) for row in summary.gainers[:movers]])]
    if summary.losers[:movers]:
        lines += ["", "🥀 <b>Уронили</b>", quote([mover(row) for row in summary.losers[:movers]])]
    facts = []
    if summary.sold_quantity:
        facts.append(f"💵 Продано: {summary.sold_quantity} шт. на {fmt_price(summary.sold_total_rub)}, прибыль {fmt_signed_price(summary.sold_profit_rub)}")
    unlocks = analytics.upcoming_unlocks
    soon = len(unlocks.today) + len(unlocks.tomorrow) + len(unlocks.next_7_days)
    if soon:
        facts.append(f"🔓 Разблокируется за 7 дней: {soon} поз.")
    if facts:
        lines += [""] + facts
    lines.append("\n<i>Рост цен и прибыль с продаж, без новых вложений. Цены Steam без комиссии.</i>")
    text = "\n".join(lines)
    if len(text) > CAPTION_LIMIT and movers > 0:
        # Long item names: fewer movers until it fits.
        return build_weekly_caption(summary, analytics, movers=movers - 1)
    return text


def build_weekly_portfolio(user, *, now: datetime | None = None):
    """(caption, PNG) for the user's week; None without purchases."""
    from modules.v2.steam_trading.investment_services import build_investment_analytics, get_investment_purchase_queryset
    from modules.v2.steam_trading.portfolio_history import build_portfolio_days, build_week_summary

    now = now or timezone.now()
    purchases = list(get_investment_purchase_queryset(user=user))
    if not purchases:
        return None
    summary = build_week_summary(purchases, now=now)
    days = build_portfolio_days(purchases, days=8, now=now)
    caption = build_weekly_caption(summary, build_investment_analytics(purchases))
    return caption, render_week_chart(days)


def send_due_weekly(*, now: datetime | None = None) -> int:
    now = now or timezone.now()
    if not is_enabled():
        return 0
    from modules.v2.steam_trading.models import SteamPurchase

    sent = 0
    linked = UserProfile.objects.exclude(Q(telegram_chat_id__isnull=True) | Q(telegram_chat_id="")).values("user_id")
    investors = SteamPurchase.objects.filter(created_by_id__in=linked).values("created_by_id")
    # The summary is on by default, so investors who never saved bot settings get theirs now.
    TelegramUserPrefs.objects.bulk_create(
        [TelegramUserPrefs(user_id=user_id) for user_id in set(investors.values_list("created_by_id", flat=True))
         .difference(TelegramUserPrefs.objects.values_list("user_id", flat=True))],
        ignore_conflicts=True,
    )
    for prefs in TelegramUserPrefs.objects.filter(weekly_portfolio_enabled=True, user_id__in=investors).select_related("user"):
        if not weekly_due(prefs, now):
            continue
        today = timezone.localdate(now)
        previous = prefs.weekly_portfolio_last_sent_on
        # Claimed before sending, so a second worker on an overlapping tick skips this user.
        claimed = TelegramUserPrefs.objects.filter(pk=prefs.pk, weekly_portfolio_last_sent_on=previous).update(
            weekly_portfolio_last_sent_on=today
        )
        if not claimed:
            continue
        try:
            sent += _send_weekly(prefs.user, now=now)
        except Exception:
            # Released again whatever broke (the chart, the data, Telegram): the next tick tries again.
            logger.warning("tg_notifier: weekly portfolio to user %s failed", prefs.user_id, exc_info=True)
            TelegramUserPrefs.objects.filter(pk=prefs.pk).update(weekly_portfolio_last_sent_on=previous)
    return sent


def _send_weekly(user, *, now: datetime) -> int:
    """1 when the summary went out, 0 when the user has nothing to get; raises when sending failed."""
    from .bot import _link

    built = build_weekly_portfolio(user, now=now) if _has_access(user) else None
    if not built:
        return 0
    caption, picture = built
    link = _link(reverse("steam_trading:investments"), "portfolio_weekly")
    markup = {"inline_keyboard": [[url_button("Открыть портфель", link)]]} if link else None
    try:
        send_photo(photo=picture, chat_id=chat_id_of(user), caption=caption, parse_mode=HTML,
                   filename="portfolio.png", content_type="image/png", reply_markup=markup)
    except TelegramNotifierError:
        logger.warning("tg_notifier: weekly portfolio picture to user %s failed, sending text", user.pk, exc_info=True)
        send_message(text=caption, chat_id=chat_id_of(user), parse_mode=HTML, reply_markup=markup)
    return 1
