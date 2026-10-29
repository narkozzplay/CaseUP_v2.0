"""The bot as a second interface: commands, the reply keyboard and the buttons under bot messages.

Only chats linked to a site account get answers (the account is found by chat id); everyone else is pointed to the
linking page. Actions that change something (favorites, alerts) check that the object belongs to that account.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Min, Q, Value, When
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile

from .delivery import HTML, site_link, url_button
from .services import TelegramNotifierError, call_bot_api, get_telegram_notifier_config, send_message

logger = logging.getLogger(__name__)

PAGE_SIZE = 10
SEARCH_LIMIT = 8
COMMANDS_PER_MINUTE = 20
AWAIT_PRICE_SECONDS = 5 * 60
TOP_CACHE_SECONDS = 5 * 60
CHANGE_PERIODS = (("За сутки", timedelta(days=1)), ("За неделю", timedelta(days=7)), ("За месяц", timedelta(days=30)))
TYPE_NAMES = {"skin": "Скины", "case": "Кейсы", "sticker": "Наклейки", "graffiti": "Граффити", "charm": "Брелоки", "other": "Другое"}
TYPE_ICONS = {"skin": "🔫", "case": "📦", "sticker": "🏷", "graffiti": "🎨", "charm": "🧿"}

BTN_PRICE = "🔎 Цена"
BTN_FAVORITES = "⭐ Избранное"
BTN_ALERTS = "🔔 Алерты"
BTN_PORTFOLIO = "💼 Портфель"
BTN_BALANCE = "💰 Баланс"
BTN_TOP = "📈 Топ"

# setMyCommands: the «Menu» button in Telegram.
BOT_COMMANDS = [
    ("price", "Цена предмета: /price AK-47 Redline"),
    ("favorites", "Избранное с ценами"),
    ("alerts", "Мои алерты: включить, выключить, удалить"),
    ("portfolio", "Портфель: стоимость и прибыль"),
    ("balance", "Баланс CaseUP"),
    ("top", "Кто вырос и упал за сутки"),
    ("help", "Что умеет бот"),
]
COMMANDS_CACHE_KEY = "tg_notifier:bot_commands:v1"

HELP_TEXT = (
    "🤖 <b>Бот CaseUP</b>\n"
    "<i>Цены CS2, алерты и сделки рынка — прямо в Telegram.</i>\n\n"
    "<b>Что я умею</b>\n"
    "<blockquote>🔎 /price название — цена и как она менялась\n"
    "⭐ /favorites — избранное с ценами\n"
    "🔔 /alerts — алерты: включить, выключить, удалить\n"
    "💼 /portfolio — стоимость портфеля и прибыль\n"
    "💰 /balance — баланс\n"
    "📈 /top — кто вырос и упал за сутки</blockquote>\n\n"
    "💡 <i>Кнопки внизу экрана делают то же самое. Алерты создаются на сайте, "
    "тихие часы и лимит уведомлений — в настройках профиля.</i>"
)


@dataclass
class Reply:
    text: str
    markup: dict[str, Any] | None = None
    # A short popup for button presses.
    toast: str = ""
    toast_alert: bool = False


# --------------------------------------------------------------------------- formatting


def fmt_price(value: Decimal | None) -> str:
    if value is None:
        return "нет цены"
    text = f"{Decimal(value):,.2f}".replace(",", " ")
    return (text[:-3] if text.endswith(".00") else text) + " ₽"


MINUS = "−"


def _sign(value: Decimal) -> str:
    return "+" if value > 0 else MINUS if value < 0 else ""


def trend(value: Decimal | None) -> str:
    """A coloured dot for a price move: green up, red down, white flat or unknown."""
    if not value:
        return "⚪"
    return "🟢" if value > 0 else "🔴"


def fmt_percent(percent: Decimal) -> str:
    return f"{_sign(percent)}{abs(percent)}%"


def fmt_signed_price(value: Decimal) -> str:
    return _sign(value) + fmt_price(abs(value))


def fmt_change(change: tuple[Decimal, Decimal]) -> str:
    rub, percent = change
    return f"{fmt_percent(percent)} · {fmt_signed_price(rub)}"


def quote(lines: list[str]) -> str:
    """A block with a coloured bar on the left, to set a group of lines apart."""
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def item_row(index: int, name: str, price: Decimal | None, percent: Decimal | None = None) -> str:
    """A numbered list line with the price (and its move) on a second, indented line."""
    tail = f"{trend(percent)} <b>{fmt_percent(percent)}</b> · {fmt_price(price)}" if percent is not None else f"💰 {fmt_price(price)}"
    return f"<b>{index}.</b> {html.escape(name)}\n     └ {tail}"


def title(icon: str, text: str, note: str = "") -> str:
    return f"{icon} <b>{text}</b>" + (f"\n<i>{note}</i>" if note else "")


def change_since(item, delta: timedelta, *, now: datetime | None = None) -> tuple[Decimal, Decimal] | None:
    """(change in ₽, change in %) of the item's price against its price `delta` ago; None without history then."""
    from modules.v2.steam_catalog.models import SWEEP_SOURCE, SteamItemPriceHistory

    if item.price_rub is None:
        return None
    now = now or timezone.now()
    baseline = (
        SteamItemPriceHistory.objects.filter(item=item, created_at__lte=now - delta)
        .exclude(source=SWEEP_SOURCE)
        .order_by("-created_at", "-id")
        .values_list("price_rub", flat=True)
        .first()
    )
    if not baseline:
        return None
    rub = (item.price_rub - baseline).quantize(Decimal("0.01"))
    percent = (rub / baseline * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return rub, percent


def main_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": BTN_PRICE}, {"text": BTN_FAVORITES}, {"text": BTN_ALERTS}],
            [{"text": BTN_PORTFOLIO}, {"text": BTN_BALANCE}, {"text": BTN_TOP}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def _numbered_buttons(ids: list[int], prefix: str, *, start: int = 1) -> list[list[dict[str, str]]]:
    buttons = [_button(str(start + index), f"{prefix}:{item_id}") for index, item_id in enumerate(ids)]
    return [buttons[index:index + 5] for index in range(0, len(buttons), 5)]


def _nav_row(prefix: str, page: int, pages: int) -> list[dict[str, str]]:
    row = []
    if page > 1:
        row.append(_button("← Назад", f"{prefix}:{page - 1}"))
    if page < pages:
        row.append(_button("Дальше →", f"{prefix}:{page + 1}"))
    return row


def _link(path: str, content: str) -> str | None:
    try:
        return site_link(path, content=content)
    except TelegramNotifierError:
        return None


# --------------------------------------------------------------------------- items


def search_items(query: str) -> tuple[list, bool]:
    """Catalog items for a free-text query: an exact Steam name wins; returns (items, more found)."""
    from modules.v2.steam_catalog.models import SteamItem

    query = " ".join(query.split())[:120]
    if not query:
        return [], False
    base = SteamItem.objects.filter(is_active=True)
    exact = base.filter(Q(market_hash_name__iexact=query) | Q(name_ru__iexact=query)).first()
    if exact is not None:
        return [exact], False
    qs = base
    for word in query.split():
        qs = qs.filter(Q(market_hash_name__icontains=word) | Q(name__icontains=word) | Q(name_ru__icontains=word))
    # Items with a price first, then each skin's variants together in wear order.
    no_price = Case(When(price_rub__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField())
    found = list(qs.order_by(no_price, "family_name", "is_stattrak", "wear_order", "market_hash_name")[:SEARCH_LIMIT + 1])
    return found[:SEARCH_LIMIT], len(found) > SEARCH_LIMIT


def _market_offer(item) -> tuple[Decimal, int] | None:
    from modules.v2.market.models import Listing

    row = Listing.objects.filter(status=Listing.Status.ACTIVE, market_hash_name=item.market_hash_name).aggregate(
        low=Min("price"), lots=Count("id")
    )
    return (row["low"], row["lots"]) if row["lots"] else None


def market_button(item) -> dict[str, str] | None:
    """«Купить на рынке» — only when CaseUP has open lots of this item."""
    from urllib.parse import urlencode

    offer = _market_offer(item)
    if offer is None:
        return None
    link = _link(f"{reverse('market:index')}?{urlencode({'q': item.market_hash_name})}", "market")
    return url_button(f"🛒 Купить на рынке · от {fmt_price(offer[0])}", link) if link else None


def item_card(user, item) -> Reply:
    from modules.v2.steam_catalog.services.browse import is_favorite

    icon = TYPE_ICONS.get(item.item_type, "🎯")
    lines = [f"{icon} <b>{html.escape(item.market_hash_name)}</b>"]
    if item.name_ru and item.name_ru != item.market_hash_name:
        lines.append(f"<i>{html.escape(item.name_ru)}</i>")
    lines.append(f"\n💰 <b>{fmt_price(item.price_rub)}</b>  ·  цена в Steam")
    if item.price_rub is not None:
        rows = []
        for label, delta in CHANGE_PERIODS:
            change = change_since(item, delta)
            if change:
                rows.append(f"{trend(change[1])} {label}: <b>{fmt_percent(change[1])}</b> · {fmt_signed_price(change[0])}")
            else:
                rows.append(f"⚪ {label}: нет данных")
        lines.append("\n📊 <b>Как менялась цена</b>")
        lines.append(quote(rows))
    if item.price_checked_at:
        lines.append(f"\n🕒 <i>Обновлено {timezone.localtime(item.price_checked_at):%d.%m в %H:%M} МСК</i>")
    return Reply("\n".join(lines), _card_markup(item, favorite=is_favorite(user, item)))


def _card_markup(item, *, favorite: bool) -> dict[str, Any]:
    rows = [[_button("★ Убрать из избранного" if favorite else "⭐ В избранное", f"fv:{item.pk}")]]
    market = market_button(item)
    if market:
        rows.append([market])
    return {"inline_keyboard": rows}


def cmd_price(user, query: str, chat_id: str) -> Reply:
    query = query.strip()
    if not query:
        cache.set(_await_key(chat_id), True, AWAIT_PRICE_SECONDS)
        return Reply("🔎 <b>Какой предмет ищем?</b>\n\nНапишите название, как в Steam, например: <code>AK-47 Redline</code>")
    items, more = search_items(query)
    if not items:
        return Reply(
            f"🤷 <b>Ничего не нашёл</b> по запросу «{html.escape(query)}».\n\n"
            "<i>Попробуйте название как в Steam, на английском: например, <code>AWP Asiimov</code>.</i>"
        )
    if len(items) == 1:
        return item_card(user, items[0])
    rows = [[_button(_short(f"{item.market_hash_name} — {fmt_price(item.price_rub)}"), f"it:{item.pk}")] for item in items]
    text = f"🔎 <b>Нашёл несколько вариантов</b> по запросу «{html.escape(query)}».\n\n👇 Выберите нужный:"
    if more:
        text += "\n<i>Показаны первые 8 — уточните запрос, если нужного нет.</i>"
    return Reply(text, {"inline_keyboard": rows})


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _await_key(chat_id: str) -> str:
    return f"tg_notifier:await_price:{chat_id}"


# --------------------------------------------------------------------------- favorites, top


def favorites_page(user, page: int = 1) -> Reply:
    from modules.v2.steam_catalog.models import SteamItemFavorite

    qs = SteamItemFavorite.objects.filter(user=user).select_related("item")
    total = qs.count()
    if not total:
        link = _link(reverse("steam_catalog:list"), "favorites")
        markup = {"inline_keyboard": [[url_button("Открыть каталог", link)]]} if link else None
        return Reply(
            "⭐ <b>В избранном пока пусто</b>\n\n"
            "<i>Добавьте предмет кнопкой «⭐ В избранное» под ответом /price или звёздочкой в каталоге.</i>",
            markup,
        )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    favorites = list(qs[start:start + PAGE_SIZE])
    note = f"{total} {_plural(total, 'предмет', 'предмета', 'предметов')} · изменение за сутки" + (f" · стр. {page}/{pages}" if pages > 1 else "")
    rows_text = []
    for index, favorite in enumerate(favorites, start=start + 1):
        item = favorite.item
        change = change_since(item, timedelta(days=1))
        rows_text.append(item_row(index, favorite.family_key, item.price_rub, change[1] if change else None))
    lines = [title("⭐", "Избранное", note), "", quote(rows_text), "", "👇 <i>Нажмите номер, чтобы открыть карточку.</i>"]
    rows = _numbered_buttons([favorite.item_id for favorite in favorites], "it", start=start + 1)
    nav = _nav_row("fl", page, pages)
    if nav:
        rows.append(nav)
    return Reply("\n".join(lines), {"inline_keyboard": rows})


def top_reply() -> Reply:
    cached = cache.get("tg_notifier:bot_top")
    if cached:
        return Reply(*cached)
    from .channel_digest import collect_digest

    digest = collect_digest(config=get_telegram_notifier_config())
    from .models import ChannelDigestSection

    lines, ids = [title("📈", "Цены CS2 за сутки", f"На {timezone.localtime():%d.%m, %H:%M} МСК")], []
    for block in digest.blocks:
        if not block.moves:
            continue
        icon = "🚀" if block.section.direction == ChannelDigestSection.Direction.UP else "🔻"
        rows = []
        for move in block.moves:
            ids.append(move.item.pk)
            rows.append(item_row(len(ids), move.item.market_hash_name, move.current_price, move.change_percent))
        lines += ["", f"{icon} <b>{html.escape(block.section.title)}</b>", quote(rows)]
    if not ids:
        return Reply("😴 <b>Рынок спит</b>\n\n<i>За сутки цены почти не двигались — заметных изменений нет.</i>")
    markup = {"inline_keyboard": _numbered_buttons(ids, "it")}
    text = "\n".join(lines) + "\n\n👇 <i>Нажмите номер, чтобы открыть карточку.</i>"
    cache.set("tg_notifier:bot_top", (text, markup), TOP_CACHE_SECONDS)
    return Reply(text, markup)


# --------------------------------------------------------------------------- alerts


def describe_rule(rule) -> str:
    from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

    target = (rule.item.market_hash_name if rule.item_id else "") or rule.family_name or TYPE_NAMES.get(rule.item_type, rule.get_item_type_display())
    if rule.kind == Rule.Kind.TARGET:
        verb = "поднимется до" if rule.direction == Rule.Direction.UP else "опустится до"
        return f"{html.escape(target)}: 🎯 цена {verb} {fmt_price(rule.target_price_rub)}"
    arrow = {"up": "📈 рост", "down": "📉 падение"}.get(rule.direction, "↕️ рост или падение")
    parts = []
    if rule.min_change_percent > 0 and rule.threshold_mode != Rule.ThresholdMode.RUB:
        parts.append(f"{rule.min_change_percent.normalize():f}%")
    if rule.min_change_rub > 0 and rule.threshold_mode != Rule.ThresholdMode.PERCENT:
        parts.append(fmt_price(rule.min_change_rub))
    joiner = " и " if rule.threshold_mode == Rule.ThresholdMode.AND else " или "
    window = {"hours": f"{rule.window_value} ч", "days": f"{rule.window_value} д"}.get(rule.window_kind, f"{rule.window_value} точек")
    return f"{html.escape(target)}: {arrow} на {joiner.join(parts) or '—'} за {window}"


def alerts_page(user, page: int = 1) -> Reply:
    from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule
    from modules.v2.subscriptions.limits import PRICE_ALERTS
    from modules.v2.subscriptions.services import get_user_limit

    qs = Rule.objects.filter(user=user).select_related("item").order_by("status", "-updated_at", "-id")
    total = qs.count()
    create_link = _link(reverse("steam_catalog:alert_rule_create"), "alerts")
    create_row = [url_button("➕ Создать на сайте", create_link)] if create_link else []
    if not total:
        return Reply(
            "🔔 <b>Алертов пока нет</b>\n\n"
            "<i>Создайте правило на сайте — пришлю сообщение, когда цена изменится или дойдёт до нужной.</i>",
            {"inline_keyboard": [create_row]} if create_row else None,
        )
    active = qs.filter(status=Rule.Status.ACTIVE).count()
    limit = get_user_limit(user, PRICE_ALERTS)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    rules = list(qs[start:start + PAGE_SIZE])
    quota = f"Работает {active} из {limit} по тарифу" if limit is not None else f"Работает {active}"
    lines = [title("🔔", "Мои алерты", quota + (f" · стр. {page}/{pages}" if pages > 1 else ""))]
    rows = []
    for index, rule in enumerate(rules, start=start + 1):
        state = "🟢 работает" if rule.status == Rule.Status.ACTIVE else "⏸ на паузе"
        lines += ["", quote([f"<b>{index}. {html.escape(rule.name)}</b>  ·  {state}", f"└ {describe_rule(rule)}"])]
        toggle = _button(f"{index}. ⏸ Выключить", f"at:{rule.pk}:{page}") if rule.status == Rule.Status.ACTIVE else _button(f"{index}. ▶ Включить", f"at:{rule.pk}:{page}")
        rows.append([toggle, _button(f"{index}. 🗑 Удалить", f"ad:{rule.pk}:{page}")])
    nav = _nav_row("al", page, pages)
    if nav:
        rows.append(nav)
    if create_row:
        rows.append(create_row)
    return Reply("\n".join(lines), {"inline_keyboard": rows})


def set_rule_active(user, rule_id: int, active: bool) -> tuple[bool, str]:
    """Switches the user's rule on or off under the plan's cap. Returns (changed, message)."""
    from modules.v2.steam_catalog.forms import alert_limit_error, lock_alert_quota
    from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

    with transaction.atomic():
        lock_alert_quota(user)
        rule = Rule.objects.select_for_update().filter(pk=rule_id, user=user).first()
        if rule is None:
            return False, "Алерт не найден — возможно, его уже удалили."
        target = Rule.Status.ACTIVE if active else Rule.Status.DISABLED
        if rule.status == target:
            return False, "Алерт уже включён." if active else "Алерт уже выключен."
        if active:
            error = alert_limit_error(user)
            if error:
                return False, error
        rule.status = target
        rule.save(update_fields=["status", "updated_at"])
    return True, "Алерт включён." if active else "Алерт выключен."


def alert_markup(rule, item, *, active: bool = True) -> dict[str, Any]:
    """Buttons under a price alert notice."""
    rows = []
    first = []
    open_link = _link(item.get_absolute_url(), "price_alert")
    if open_link:
        first.append(url_button("Открыть предмет", open_link))
    first.append(_button("⏸ Выключить", f"ax:{rule.pk}") if active else _button("▶ Включить снова", f"ae:{rule.pk}"))
    rows.append(first)
    second = []
    edit_link = _link(reverse("steam_catalog:alert_rule_edit", args=[rule.pk]), "price_alert")
    if edit_link:
        second.append(url_button("Изменить порог", edit_link))
    market = market_button(item)
    if market:
        second.append(market)
    if second:
        rows.append(second)
    return {"inline_keyboard": rows}


# --------------------------------------------------------------------------- money


def portfolio_analytics(user):
    """Investment analytics over all of the user's purchases (totals must not miss old ones); None without any."""
    from modules.v2.steam_trading.investment_services import build_investment_analytics, get_investment_purchase_queryset

    purchases = list(get_investment_purchase_queryset(user=user))
    return build_investment_analytics(purchases) if purchases else None


def portfolio_summary(user, analytics=None) -> str | None:
    """A few lines on the user's investments; None when they have none."""
    analytics = analytics or portfolio_analytics(user)
    if analytics is None:
        return None
    kpi = analytics.kpi
    if not kpi.active_purchases_count:
        return quote(["📭 Активных позиций нет", f"💵 Заработано продажами: <b>{fmt_signed_price(kpi.realized_pnl_sum)}</b>"])
    percent = (kpi.unrealized_pnl_sum / kpi.active_cost_basis_sum * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if kpi.active_cost_basis_sum else Decimal("0")
    return quote([
        f"💵 Вложено: <b>{fmt_price(kpi.active_cost_basis_sum)}</b>",
        f"📦 Стоит сейчас: <b>{fmt_price(kpi.current_active_value_sum)}</b>",
        f"{trend(kpi.unrealized_pnl_sum)} Прибыль: <b>{fmt_signed_price(kpi.unrealized_pnl_sum)} ({fmt_percent(percent)})</b>",
        f"🎒 Позиций: <b>{kpi.active_purchases_count}</b>",
    ])


def _plural(number: int, one: str, few: str, many: str) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def portfolio_reply(user) -> Reply:
    link = _link(reverse("steam_trading:investments"), "portfolio")
    analytics = portfolio_analytics(user)
    summary = portfolio_summary(user, analytics)
    if summary is None:
        add_link = _link(reverse("steam_trading:add"), "portfolio")
        markup = {"inline_keyboard": [[url_button("Добавить покупку", add_link)]]} if add_link else None
        return Reply(
            "💼 <b>В портфеле пока нет покупок</b>\n\n<i>Добавьте их на сайте — я буду считать стоимость и прибыль.</i>", markup
        )
    lines = [title("💼", "Портфель"), "", summary]

    def mover(row) -> str:
        percent = Decimal(row.unrealized_pnl_percent).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{trend(percent)} <b>{fmt_percent(percent)}</b>  {html.escape(row.purchase.item.market_hash_name)}"

    gainers = [row for row in analytics.top_gainers if row.unrealized_pnl_percent > 0][:3]
    losers = [row for row in analytics.top_losers if row.unrealized_pnl_percent < 0][:3]
    if gainers:
        lines += ["", "🏆 <b>Лучшие</b>", quote([mover(row) for row in gainers])]
    if losers:
        lines += ["", "🥀 <b>Худшие</b>", quote([mover(row) for row in losers])]
    lines.append("\nℹ️ <i>По текущим ценам Steam, без комиссии 15%.</i>")
    markup = {"inline_keyboard": [[url_button("Открыть портфель на сайте", link)]]} if link else None
    return Reply("\n".join(lines), markup)


def balance_reply(user) -> Reply:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    rows = [f"💳 Основной: <b>{fmt_price(profile.balance_coins)}</b>"]
    if profile.market_balance_coins:
        rows.append(f"🏪 С продаж на рынке: <b>{fmt_price(profile.market_balance_coins)}</b>")
        rows.append("<i>└ эти деньги можно вывести</i>")
    lines = [title("💰", "Баланс"), "", quote(rows)]
    row = []
    topup = _link(reverse("payments:topup"), "balance")
    if topup:
        row.append(url_button("Пополнить", topup))
    if profile.market_balance_coins:
        payout = _link(reverse("market:payout"), "balance")
        if payout:
            row.append(url_button("Вывести", payout))
    return Reply("\n".join(lines), {"inline_keyboard": [row]} if row else None)


# --------------------------------------------------------------------------- dispatch


def _user_for_chat(chat_id: str):
    profile = UserProfile.objects.select_related("user").filter(telegram_chat_id=chat_id).first()
    return profile.user if profile and profile.user.is_active else None


def _unlinked_reply() -> Reply:
    link = _link(reverse("profiles:profile"), "link")
    text = (
        "👋 <b>Это бот CaseUP</b>\n\n"
        "Чтобы пользоваться им, привяжите Telegram к аккаунту:\n"
        "<blockquote>1. Откройте профиль на сайте\n2. Нажмите «Подключить Telegram»</blockquote>"
    )
    return Reply(text, {"inline_keyboard": [[url_button("Открыть профиль", link)]]} if link else None)


def _rate_limited(chat_id: str) -> tuple[bool, bool]:
    """(over the limit, tell the user about it now)."""
    key = f"tg_notifier:rate:{chat_id}:{int(timezone.now().timestamp() // 60)}"
    if cache.add(key, 1, 90):
        return False, False
    try:
        count = cache.incr(key)
    except ValueError:
        return False, False
    if count <= COMMANDS_PER_MINUTE:
        return False, False
    return True, count == COMMANDS_PER_MINUTE + 1


def welcome_reply() -> Reply:
    return Reply("✅ <b>Telegram подключён к вашему профилю CaseUP!</b>\n\n" + HELP_TEXT, main_keyboard())


KEYBOARD_COMMANDS = {
    BTN_PRICE: "price",
    BTN_FAVORITES: "favorites",
    BTN_ALERTS: "alerts",
    BTN_PORTFOLIO: "portfolio",
    BTN_BALANCE: "balance",
    BTN_TOP: "top",
}


def parse_command(text: str) -> tuple[str, str] | None:
    """"/price@CaseUpBot AK" -> ("price", "AK"); keyboard buttons count as commands too."""
    text = (text or "").strip()
    if text in KEYBOARD_COMMANDS:
        return KEYBOARD_COMMANDS[text], ""
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name = head[1:].split("@", 1)[0].lower()
    return name, rest.strip()


def reply_to_message(chat_id: str, text: str) -> Reply | None:
    """What the bot answers to a text message from a private chat (the /start link token is handled before)."""
    user = _user_for_chat(chat_id)
    if user is None:
        return _unlinked_reply()
    command = parse_command(text)
    if command is None:
        if cache.get(_await_key(chat_id)):
            cache.delete(_await_key(chat_id))
            return cmd_price(user, text, chat_id)
        return Reply("💡 Чтобы узнать цену, нажмите «🔎 Цена» или напишите <code>/price название</code>.\nВсе команды — /help.", main_keyboard())
    name, argument = command
    cache.delete(_await_key(chat_id))
    if name == "price":
        return cmd_price(user, argument, chat_id)
    if name == "favorites":
        return favorites_page(user)
    if name == "alerts":
        return alerts_page(user)
    if name == "portfolio":
        return portfolio_reply(user)
    if name == "balance":
        return balance_reply(user)
    if name == "top":
        return top_reply()
    if name in ("start", "help", "menu"):
        return Reply(HELP_TEXT, main_keyboard())
    return Reply("🤔 Такой команды нет. Список команд — /help.", main_keyboard())


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "").strip()
    if chat.get("type", "private") != "private":
        return {"ok": True, "ignored": "not_private"}
    limited, warn = _rate_limited(chat_id)
    if limited:
        if warn:
            _send(chat_id, Reply("Слишком много запросов. Подождите минуту."))
        return {"ok": True, "ignored": "rate_limited"}
    reply = reply_to_message(chat_id, message.get("text") or "")
    if reply is not None:
        _send(chat_id, reply)
    return {"ok": True, "handled": "message"}


def _send(chat_id: str, reply: Reply) -> None:
    try:
        send_message(text=reply.text, chat_id=chat_id, parse_mode=HTML, reply_markup=reply.markup)
    except TelegramNotifierError:
        logger.warning("tg_notifier: reply to chat %s failed", chat_id, exc_info=True)


# --------------------------------------------------------------------------- buttons


def reply_to_callback(chat_id: str, data: str) -> tuple[Reply | None, str]:
    """(new content, how to show it): "edit" the pressed message, "markup" (buttons only), "send" a new one,
    or "toast" only."""
    user = _user_for_chat(chat_id)
    if user is None:
        return _unlinked_reply(), "send"
    kind, _, rest = data.partition(":")
    args = rest.split(":")
    try:
        numbers = [int(value) for value in args if value]
    except ValueError:
        return Reply("", toast="Кнопка устарела."), "toast"

    if kind == "it" and numbers:
        from modules.v2.steam_catalog.models import SteamItem

        item = SteamItem.objects.filter(pk=numbers[0], is_active=True).first()
        if item is None:
            return Reply("", toast="Предмет не найден."), "toast"
        return item_card(user, item), "send"
    if kind == "fv" and numbers:
        from modules.v2.steam_catalog.models import SteamItem
        from modules.v2.steam_catalog.services.browse import toggle_favorite

        item = SteamItem.objects.filter(pk=numbers[0]).first()
        if item is None:
            return Reply("", toast="Предмет не найден."), "toast"
        state = toggle_favorite(user, item)
        return Reply("", _card_markup(item, favorite=state), toast="Добавлено в избранное" if state else "Убрано из избранного"), "markup"
    if kind == "fl" and numbers:
        return favorites_page(user, numbers[0]), "edit"
    if kind == "al" and numbers:
        return alerts_page(user, numbers[0]), "edit"
    if kind == "at" and len(numbers) == 2:
        from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

        rule = Rule.objects.filter(pk=numbers[0], user=user).first()
        if rule is None:
            return Reply("", toast="Алерт не найден."), "toast"
        changed, message = set_rule_active(user, rule.pk, rule.status != Rule.Status.ACTIVE)
        page = alerts_page(user, numbers[1])
        page.toast, page.toast_alert = message, not changed
        return page, "edit"
    if kind == "ad" and len(numbers) == 2:
        from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

        rule = Rule.objects.filter(pk=numbers[0], user=user).first()
        if rule is None:
            return Reply("", toast="Алерт не найден."), "toast"
        markup = {"inline_keyboard": [[_button("🗑 Да, удалить", f"aD:{rule.pk}:{numbers[1]}"), _button("Отмена", f"al:{numbers[1]}")]]}
        return Reply(
            f"🗑 <b>Удалить алерт «{html.escape(rule.name)}»?</b>\n\n{quote([describe_rule(rule)])}\n\n"
            "<i>История его срабатываний тоже удалится.</i>",
            markup,
        ), "edit"
    if kind == "aD" and len(numbers) == 2:
        from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

        deleted, _ = Rule.objects.filter(pk=numbers[0], user=user).delete()
        page = alerts_page(user, numbers[1])
        page.toast = "Алерт удалён." if deleted else "Алерт уже удалён."
        return page, "edit"
    if kind in ("ax", "ae") and numbers:
        from modules.v2.steam_catalog.models import UserPriceAlertRule as Rule

        rule = Rule.objects.select_related("item").filter(pk=numbers[0], user=user).first()
        if rule is None:
            return Reply("", toast="Алерт не найден — возможно, его уже удалили.", toast_alert=True), "toast"
        changed, message = set_rule_active(user, rule.pk, kind == "ae")
        rule.refresh_from_db()
        if rule.item_id:
            markup = alert_markup(rule, rule.item, active=rule.status == Rule.Status.ACTIVE)
            return Reply("", markup, toast=message, toast_alert=not changed), "markup"
        return Reply("", toast=message, toast_alert=not changed), "toast"
    return Reply("", toast="Кнопка устарела."), "toast"


def handle_callback(callback: dict[str, Any]) -> dict[str, Any]:
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "").strip()
    callback_id = callback.get("id")
    if not chat_id or chat.get("type", "private") != "private":
        _answer(callback_id, "")
        return {"ok": True, "ignored": "not_private"}
    limited, _warn = _rate_limited(chat_id)
    if limited:
        _answer(callback_id, "Слишком много нажатий. Подождите минуту.")
        return {"ok": True, "ignored": "rate_limited"}

    reply, mode = reply_to_callback(chat_id, str(callback.get("data") or ""))
    _answer(callback_id, reply.toast if reply else "", alert=bool(reply and reply.toast_alert))
    if reply is None or mode == "toast":
        return {"ok": True, "handled": "callback"}
    message_id = message.get("message_id")
    try:
        if mode == "edit" and message_id:
            call_bot_api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": reply.text, "parse_mode": HTML, "disable_web_page_preview": True, "reply_markup": reply.markup or {"inline_keyboard": []}})
        elif mode == "markup" and message_id:
            call_bot_api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply.markup or {"inline_keyboard": []}})
        else:
            _send(chat_id, reply)
    except TelegramNotifierError as exc:
        if "message is not modified" not in str(exc):
            logger.warning("tg_notifier: callback answer to chat %s failed", chat_id, exc_info=True)
    return {"ok": True, "handled": "callback"}


def _answer(callback_id, text: str, *, alert: bool = False) -> None:
    if not callback_id:
        return
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
        payload["show_alert"] = alert
    try:
        call_bot_api("answerCallbackQuery", payload)
    except TelegramNotifierError:
        logger.info("tg_notifier: answerCallbackQuery failed", exc_info=True)


def ensure_commands() -> None:
    """Publishes the command menu once a day (cheap to repeat, Telegram keeps it)."""
    if cache.get(COMMANDS_CACHE_KEY):
        return
    try:
        call_bot_api("setMyCommands", {"commands": [{"command": name, "description": text} for name, text in BOT_COMMANDS]})
    except TelegramNotifierError:
        logger.info("tg_notifier: setMyCommands failed", exc_info=True)
        return
    cache.set(COMMANDS_CACHE_KEY, True, 24 * 3600)
