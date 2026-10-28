from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from http.client import HTTPMessage
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem

from .models import FarmAccount, FarmAccountInventoryItem, FarmAccountInventorySync, WeeklyDropLog

DROP_WEEK_ANCHOR = date(2026, 1, 1)
DROP_WEEK_START_WEEKDAY = 2  # Wednesday
STEAM_ID64_BASE = 76561197960265728
PAYOUT_RATE = Decimal("0.65")
STEAM_INVENTORY_PAGE_SIZE = 2000

MONTHS_RU = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


@dataclass(frozen=True)
class DropWeek:
    index: int
    start: date
    end: date

    @property
    def month_year_label(self) -> str:
        start_label = f"{MONTHS_RU[self.start.month]} {self.start.year}"
        end_label = f"{MONTHS_RU[self.end.month]} {self.end.year}"
        if self.start.month == self.end.month and self.start.year == self.end.year:
            return start_label
        return f"{start_label} / {end_label}"

    @property
    def range_label(self) -> str:
        return f"{self.start:%d.%m.%Y} — {self.end:%d.%m.%Y}"

    @property
    def label(self) -> str:
        return f"Неделя #{self.index:02d} — {self.month_year_label} · {self.range_label}"


@dataclass(frozen=True)
class ParsedTradeUrl:
    partner_id: str
    token: str
    steam_id64: str


def get_drop_week_zero_start() -> date:
    anchor_weekday = DROP_WEEK_ANCHOR.weekday()
    delta = (anchor_weekday - DROP_WEEK_START_WEEKDAY) % 7
    return DROP_WEEK_ANCHOR - timedelta(days=delta)


def get_drop_week_by_index(week_index: int) -> DropWeek:
    if week_index < 1:
        raise ValueError("week_index must be >= 1")

    start = get_drop_week_zero_start() + timedelta(days=(week_index - 1) * 7)
    end = start + timedelta(days=6)
    return DropWeek(index=week_index, start=start, end=end)


def get_drop_week_for_date(target_date: date) -> DropWeek:
    start_zero = get_drop_week_zero_start()
    if target_date < start_zero:
        raise ValueError("target_date is earlier than drop week baseline")

    delta_days = (target_date - start_zero).days
    week_index = (delta_days // 7) + 1
    return get_drop_week_by_index(week_index)


def get_current_drop_week(today: date | None = None) -> DropWeek:
    today = today or date.today()
    return get_drop_week_for_date(today)


def get_drop_week_choices(today: date | None = None) -> list[tuple[str, str]]:
    current = get_current_drop_week(today=today)
    weeks = [get_drop_week_by_index(index) for index in range(1, current.index + 1)]
    weeks.reverse()
    return [(str(week.index), week.label) for week in weeks]


def create_weekly_drop(
    *,
    account,
    week_index: int,
    item_1=None,
    item_2=None,
    notes: str = "",
    created_by=None,
) -> WeeklyDropLog:
    week = get_drop_week_by_index(week_index)

    with transaction.atomic():
        drop = WeeklyDropLog(
            account=account,
            week_index=week.index,
            week_start=week.start,
            week_end=week.end,
            item_1=item_1,
            item_2=item_2,
            notes=(notes or "").strip(),
            created_by=created_by,
        )
        drop.full_clean()
        drop.save()
        return drop


def parse_trade_url(trade_url: str) -> ParsedTradeUrl:
    value = (trade_url or "").strip()
    if not value:
        raise ValueError("Trade URL пустой.")

    parsed = urlparse(value)
    query = parse_qs(parsed.query)

    partner = (query.get("partner") or [""])[0].strip()
    token = (query.get("token") or [""])[0].strip()

    if not partner.isdigit():
        raise ValueError("В trade URL отсутствует корректный partner.")
    if not token:
        raise ValueError("В trade URL отсутствует token.")

    steam_id64 = str(STEAM_ID64_BASE + int(partner))
    return ParsedTradeUrl(
        partner_id=partner,
        token=token,
        steam_id64=steam_id64,
    )


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decode_response_payload(raw: bytes, headers: HTTPMessage | None = None) -> str:
    charset = "utf-8"
    if headers is not None:
        try:
            charset = headers.get_content_charset() or "utf-8"
        except Exception:
            charset = "utf-8"
    return raw.decode(charset, errors="replace")


def _build_inventory_request_headers(steam_id64: str) -> dict[str, str]:
    profile_url = f"https://steamcommunity.com/profiles/{steam_id64}/inventory"

    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": profile_url,
    }


def _normalize_inventory_error(http_code: int, payload: str) -> str:
    text = (payload or "").strip()
    lower = text.lower()

    if http_code == 429 or "too many requests" in lower:
        return "Steam временно ограничил запросы к inventory. Повтори синхронизацию позже."

    if "this profile is private" in lower or "inventory is private" in lower:
        return "Inventory закрыт приватностью Steam. Сделай профиль и инвентарь публичными."

    if "inventory unavailable" in lower or "inventory not available" in lower:
        return "Steam inventory недоступен для чтения. Проверь публичность профиля и наличие CS2 inventory."

    if "unsupported request" in lower:
        return "Steam отклонил запрос к inventory. Обычно это закрытый inventory, пустой CS2 inventory или временный сбой Steam."

    if http_code == 400:
        return "Steam вернул HTTP 400 при чтении inventory. Обычно это закрытый inventory, пустой CS2 inventory или временный сбой Steam."

    if http_code == 403:
        return "Steam запретил доступ к inventory (HTTP 403). Возможен rate limit или временная защита Steam."

    return f"Steam inventory HTTP {http_code}"


def _steam_inventory_fetch_page(steam_id64: str, start_assetid: str | None = None) -> dict:
    params = {
        "l": "english",
        "count": str(STEAM_INVENTORY_PAGE_SIZE),
        "norender": "1",
    }
    if start_assetid:
        params["start_assetid"] = start_assetid

    url = f"https://steamcommunity.com/inventory/{steam_id64}/730/2?{urlencode(params)}"
    request = Request(
        url,
        headers=_build_inventory_request_headers(steam_id64),
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = _decode_response_payload(response.read(), response.headers)
    except HTTPError as exc:
        error_payload = ""
        try:
            error_payload = _decode_response_payload(exc.read(), exc.headers)
        except Exception:
            error_payload = ""

        raise RuntimeError(_normalize_inventory_error(exc.code, error_payload)) from exc
    except URLError as exc:
        raise RuntimeError("Не удалось подключиться к Steam inventory.") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Steam inventory вернул невалидный JSON.") from exc

    if not data.get("success"):
        error_payload = json.dumps(data, ensure_ascii=False)
        raise RuntimeError(_normalize_inventory_error(400, error_payload))

    return data


def _rarity_color(description: dict) -> str:
    for tag in description.get("tags") or []:
        if tag.get("category") == "Rarity":
            color = str(tag.get("color") or "").strip().lstrip("#")
            if len(color) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in color):
                return color.lower()
    return ""


def _asset_float_values(asset_properties: list) -> dict[str, float]:
    """Steam returns the wear rating per asset in `asset_properties`."""
    values: dict[str, float] = {}
    for entry in asset_properties:
        if not isinstance(entry, dict):
            continue
        for prop in entry.get("asset_properties") or []:
            if not isinstance(prop, dict):
                continue
            if prop.get("propertyid") != 2 and "wear" not in str(prop.get("name") or "").lower():
                continue
            try:
                value = float(prop.get("float_value"))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 1:
                values[str(entry.get("assetid") or "")] = value
    return values


def fetch_public_inventory(steam_id64: str) -> list[dict]:
    items: list[dict] = []
    start_assetid: str | None = None

    while True:
        data = _steam_inventory_fetch_page(steam_id64, start_assetid=start_assetid)

        descriptions = {}
        for description in data.get("descriptions", []):
            key = (
                str(description.get("classid") or ""),
                str(description.get("instanceid") or "0"),
            )
            descriptions[key] = description

        float_by_asset = _asset_float_values(data.get("asset_properties") or [])

        for asset in data.get("assets", []):
            class_id = str(asset.get("classid") or "")
            instance_id = str(asset.get("instanceid") or "0")
            desc = descriptions.get((class_id, instance_id), {})

            items.append(
                {
                    "asset_id": str(asset.get("assetid") or ""),
                    "class_id": class_id,
                    "instance_id": instance_id,
                    "amount": int(asset.get("amount") or 1),
                    "market_hash_name": (desc.get("market_hash_name") or desc.get("market_name") or "").strip(),
                    "icon_url": (desc.get("icon_url_large") or desc.get("icon_url") or "").strip(),
                    "rarity_color": _rarity_color(desc),
                    "float_value": float_by_asset.get(str(asset.get("assetid") or "")),
                    "tradable": bool(desc.get("tradable")),
                    "marketable": bool(desc.get("marketable")),
                }
            )

        if data.get("more_items"):
            start_assetid = str(data.get("last_assetid") or "")
            if not start_assetid:
                break
            continue

        break

    return [
        item
        for item in items
        if item["asset_id"]
        and item["market_hash_name"]
        and item["tradable"]
    ]


def _build_sellability(*, matched_item: SteamItem | None, tradable: bool) -> tuple[bool, str, Decimal | None, Decimal | None]:
    if not tradable:
        return False, "Steam помечает предмет как нетрейдабельный.", None, None

    if matched_item is None:
        return False, "Предмет отсутствует в Steam Catalog.", None, None

    if not matched_item.allow_sell:
        return False, "Продажа предмета отключена в Steam Catalog.", matched_item.price_rub, None

    if matched_item.price_rub is None:
        return False, "У предмета нет цены в Steam Catalog.", None, None

    payout = _quantize_money(matched_item.price_rub * PAYOUT_RATE)
    return True, "", matched_item.price_rub, payout


def sync_account_inventory(*, account: FarmAccount) -> FarmAccountInventorySync:
    parsed = parse_trade_url(account.trade_url)

    if account.steam_id64 and account.steam_id64 != parsed.steam_id64:
        raise ValueError("steam_id64 аккаунта не совпадает с trade URL.")

    sync_run = FarmAccountInventorySync.objects.create(
        account=account,
        status=FarmAccountInventorySync.Status.PENDING,
    )

    try:
        inventory_rows = [
            row
            for row in fetch_public_inventory(parsed.steam_id64)
            if row.get("tradable")
        ]
        
        market_names = sorted(
            {
                row["market_hash_name"]
                for row in inventory_rows
                if row["market_hash_name"]
            }
        )
        catalog_map = SteamItem.objects.filter(
            market_hash_name__in=market_names
        ).in_bulk(field_name="market_hash_name")

        now = timezone.now()

        with transaction.atomic():
            if account.steam_id64 != parsed.steam_id64:
                account.steam_id64 = parsed.steam_id64
                account.save(update_fields=["steam_id64", "updated_at"])

            existing_items = {
                item.asset_id: item
                for item in FarmAccountInventoryItem.objects.select_for_update().filter(account=account)
            }

            seen_asset_ids: set[str] = set()

            for row in inventory_rows:
                matched_item = catalog_map.get(row["market_hash_name"])
                is_sellable, block_reason, catalog_price, payout_price = _build_sellability(
                    matched_item=matched_item,
                    tradable=row["tradable"],
                )

                asset_id = row["asset_id"]
                seen_asset_ids.add(asset_id)

                item = existing_items.get(asset_id)
                if item is None:
                    FarmAccountInventoryItem.objects.create(
                        account=account,
                        sync_run=sync_run,
                        asset_id=asset_id,
                        class_id=row["class_id"],
                        instance_id=row["instance_id"],
                        amount=row["amount"],
                        market_hash_name=row["market_hash_name"],
                        icon_url=row["icon_url"],
                        rarity_color=row.get("rarity_color", ""),
                        float_value=row.get("float_value"),
                        tradable=row["tradable"],
                        marketable=row["marketable"],
                        matched_item=matched_item,
                        catalog_price_rub_snapshot=catalog_price,
                        payout_price_rub_snapshot=payout_price,
                        is_sellable=is_sellable,
                        sell_block_reason=block_reason,
                        is_missing=False,
                        sale_state=FarmAccountInventoryItem.SaleState.AVAILABLE,
                    )
                    continue

                item.sync_run = sync_run
                item.class_id = row["class_id"]
                item.instance_id = row["instance_id"]
                item.amount = row["amount"]
                item.market_hash_name = row["market_hash_name"]
                item.icon_url = row["icon_url"]
                item.rarity_color = row.get("rarity_color", "")
                item.float_value = row.get("float_value")
                item.tradable = row["tradable"]
                item.marketable = row["marketable"]
                item.matched_item = matched_item
                item.catalog_price_rub_snapshot = catalog_price
                item.payout_price_rub_snapshot = payout_price
                item.is_sellable = is_sellable
                item.sell_block_reason = block_reason
                item.is_missing = False
                item.last_seen_at = now
                item.save(
                    update_fields=[
                        "sync_run",
                        "class_id",
                        "instance_id",
                        "amount",
                        "market_hash_name",
                        "icon_url",
                        "rarity_color",
                        "float_value",
                        "tradable",
                        "marketable",
                        "matched_item",
                        "catalog_price_rub_snapshot",
                        "payout_price_rub_snapshot",
                        "is_sellable",
                        "sell_block_reason",
                        "is_missing",
                        "last_seen_at",
                    ]
                )

            missing_qs = FarmAccountInventoryItem.objects.select_for_update().filter(account=account)
            if seen_asset_ids:
                missing_qs = missing_qs.exclude(asset_id__in=seen_asset_ids)

            missing_qs.update(
                sync_run=sync_run,
                is_missing=True,
                is_sellable=False,
                sell_block_reason="Предмет не найден в последнем публичном inventory snapshot.",
                last_seen_at=now,
            )

            total_sellable = FarmAccountInventoryItem.objects.filter(
                account=account,
                is_missing=False,
                is_sellable=True,
            ).count()

            sync_run.status = FarmAccountInventorySync.Status.SUCCESS
            sync_run.total_assets = len(seen_asset_ids)
            sync_run.total_sellable = total_sellable
            sync_run.finished_at = now
            sync_run.error_text = ""
            sync_run.save(
                update_fields=[
                    "status",
                    "total_assets",
                    "total_sellable",
                    "finished_at",
                    "error_text",
                ]
            )

        return sync_run
    except Exception as exc:
        sync_run.status = FarmAccountInventorySync.Status.FAILED
        sync_run.error_text = str(exc)
        sync_run.finished_at = timezone.now()
        sync_run.save(update_fields=["status", "error_text", "finished_at"])
        raise
# --- Farm analytics / subscriptions / notifications expansion ---

from django.db.models import Sum
from django.urls import reverse

from modules.v2.subscriptions.feature_codes import (
    FARM_ANALYTICS_DEEP,
    FARM_BONUS_PROGRAM,
    FARM_CASEUP_REDUCED_COMMISSION,
    FARM_TG_ALERTS,
)
from modules.v2.subscriptions.services import get_feature_access_state, user_has_feature
from modules.v2.system.services import get_public_base_url, is_module_enabled
from modules.v2.tg_notifier.services import send_user_message

from .models import (
    FarmDropItem,
    FarmDropSale,
    FarmDropSession,
    FarmModuleConfig,
    FarmerRewardGrant,
    FarmerRewardTier,
    FarmNotificationEvent,
)

ONE_HUNDRED = Decimal("100")
ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class FarmFeatureState:
    deep_analytics: bool
    tg_alerts: bool
    reduced_commission: bool
    bonus_program: bool
    deep_access_state: object


@dataclass(frozen=True)
class FarmItemAnalytics:
    item: FarmDropItem
    collected_unit_price_rub: Decimal
    current_unit_price_rub: Decimal
    collected_total_rub: Decimal
    current_remaining_value_rub: Decimal
    unit_delta_rub: Decimal
    unit_delta_percent: Decimal
    sold_quantity: int
    remaining_quantity: int
    realized_pnl_rub: Decimal
    unrealized_pnl_rub: Decimal
    total_pnl_rub: Decimal


@dataclass(frozen=True)
class FarmSessionAnalytics:
    session: FarmDropSession
    collected_value_rub: Decimal
    current_value_rub: Decimal
    delta_rub: Decimal
    delta_percent: Decimal
    realized_pnl_rub: Decimal
    unrealized_pnl_rub: Decimal
    total_pnl_rub: Decimal
    sold_quantity: int
    remaining_quantity: int
    item_analytics: tuple[FarmItemAnalytics, ...]


@dataclass(frozen=True)
class PlatformBreakdownRow:
    platform: str
    platform_label: str
    sales_count: int
    gross_sale_rub: Decimal
    net_sale_rub: Decimal
    profit_rub: Decimal
    commission_saved_rub: Decimal


@dataclass(frozen=True)
class FarmerRewardProgress:
    turnover_rub: Decimal
    current_tier: FarmerRewardTier | None
    next_tier: FarmerRewardTier | None
    remaining_to_next_rub: Decimal
    grants: tuple[FarmerRewardGrant, ...]


@dataclass(frozen=True)
class FarmDashboardAnalytics:
    collected_value_rub: Decimal
    current_value_rub: Decimal
    delta_rub: Decimal
    delta_percent: Decimal
    realized_pnl_rub: Decimal
    unrealized_pnl_rub: Decimal
    total_pnl_rub: Decimal
    sold_positions_count: int
    open_positions_count: int
    caseup_turnover_rub: Decimal
    commission_saved_rub: Decimal
    session_analytics: tuple[FarmSessionAnalytics, ...]
    best_positions: tuple[FarmItemAnalytics, ...]
    worst_positions: tuple[FarmItemAnalytics, ...]
    platform_breakdown: tuple[PlatformBreakdownRow, ...]
    reward_progress: FarmerRewardProgress


@dataclass(frozen=True)
class FarmLockedSessionRow:
    session: FarmDropSession


@dataclass(frozen=True)
class FarmLockedItemRow:
    item: FarmDropItem
    sold_quantity: int
    remaining_quantity: int


@dataclass(frozen=True)
class FarmCaseupAdvantage:
    item: FarmDropItem
    current_price_rub: Decimal
    external_net_rub: Decimal
    caseup_net_rub: Decimal
    advantage_rub: Decimal



def q_money(value: Decimal | int | float | str | None) -> Decimal:
    return _quantize_money(Decimal(str(value or "0")))



def q_percent(value: Decimal | int | float | str | None) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



def get_farm_module_config() -> FarmModuleConfig:
    config = FarmModuleConfig.objects.filter(singleton_key=1).first()
    if config is not None:
        return config
    config, _ = FarmModuleConfig.objects.get_or_create(singleton_key=1, defaults=FarmModuleConfig.bootstrap_defaults())
    return config



def get_farmer_feature_state(user) -> FarmFeatureState:
    deep_access = get_feature_access_state(user, FARM_ANALYTICS_DEEP, family="farmer")
    return FarmFeatureState(
        deep_analytics=deep_access.is_available,
        tg_alerts=user_has_feature(user, FARM_TG_ALERTS, family="farmer"),
        reduced_commission=user_has_feature(user, FARM_CASEUP_REDUCED_COMMISSION, family="farmer"),
        bonus_program=user_has_feature(user, FARM_BONUS_PROGRAM, family="farmer"),
        deep_access_state=deep_access,
    )



def resolve_sale_fee_percent(*, user, platform: str) -> Decimal:
    config = get_farm_module_config()
    if platform == FarmDropSale.Platform.CASEUP:
        if user_has_feature(user, FARM_CASEUP_REDUCED_COMMISSION, family="farmer"):
            return q_percent(config.subscribed_caseup_fee_percent)
        return q_percent(config.default_caseup_fee_percent)
    return q_percent(config.external_reference_fee_percent)



def current_item_price_rub(item: FarmDropItem) -> Decimal:
    if item.steam_item_id and item.steam_item and item.steam_item.price_rub is not None:
        return q_money(item.steam_item.price_rub)
    return q_money(item.collected_price_rub_snapshot)



def calculate_item_analytics(item: FarmDropItem) -> FarmItemAnalytics:
    sales = list(item.sales.all())
    collected_unit = q_money(item.collected_price_rub_snapshot)
    current_unit = current_item_price_rub(item)
    collected_total = q_money(collected_unit * item.quantity)
    current_remaining = q_money(current_unit * item.remaining_quantity)
    sold_quantity = item.sold_quantity

    realized = q_money(sum((sale.profit_rub for sale in sales), ZERO_MONEY))
    remaining_cost_basis = q_money(collected_unit * item.remaining_quantity)
    unrealized = q_money(current_remaining - remaining_cost_basis)
    total = q_money(realized + unrealized)
    unit_delta = q_money(current_unit - collected_unit)
    unit_delta_percent = q_percent((unit_delta / collected_unit) * ONE_HUNDRED) if collected_unit > ZERO_MONEY else Decimal("0.00")

    return FarmItemAnalytics(
        item=item,
        collected_unit_price_rub=collected_unit,
        current_unit_price_rub=current_unit,
        collected_total_rub=collected_total,
        current_remaining_value_rub=current_remaining,
        unit_delta_rub=unit_delta,
        unit_delta_percent=unit_delta_percent,
        sold_quantity=sold_quantity,
        remaining_quantity=item.remaining_quantity,
        realized_pnl_rub=realized,
        unrealized_pnl_rub=unrealized,
        total_pnl_rub=total,
    )



def calculate_session_analytics(session: FarmDropSession) -> FarmSessionAnalytics:
    items = list(session.items.select_related("steam_item").prefetch_related("sales"))
    item_rows = tuple(calculate_item_analytics(item) for item in items)
    collected = q_money(sum((row.collected_total_rub for row in item_rows), ZERO_MONEY))
    current = q_money(sum((row.current_remaining_value_rub for row in item_rows), ZERO_MONEY))
    delta = q_money(current - collected)
    delta_percent = q_percent((delta / collected) * ONE_HUNDRED) if collected > ZERO_MONEY else Decimal("0.00")
    realized = q_money(sum((row.realized_pnl_rub for row in item_rows), ZERO_MONEY))
    unrealized = q_money(sum((row.unrealized_pnl_rub for row in item_rows), ZERO_MONEY))
    total = q_money(realized + unrealized)
    sold_quantity = sum(row.sold_quantity for row in item_rows)
    remaining_quantity = sum(row.remaining_quantity for row in item_rows)
    return FarmSessionAnalytics(
        session=session,
        collected_value_rub=collected,
        current_value_rub=current,
        delta_rub=delta,
        delta_percent=delta_percent,
        realized_pnl_rub=realized,
        unrealized_pnl_rub=unrealized,
        total_pnl_rub=total,
        sold_quantity=sold_quantity,
        remaining_quantity=remaining_quantity,
        item_analytics=item_rows,
    )


def build_locked_session_rows(sessions) -> tuple[FarmLockedSessionRow, ...]:
    return tuple(FarmLockedSessionRow(session=session) for session in sessions)



def build_locked_item_rows(session: FarmDropSession) -> tuple[FarmLockedItemRow, ...]:
    items = list(session.items.select_related("steam_item").prefetch_related("sales"))
    return tuple(
        FarmLockedItemRow(
            item=item,
            sold_quantity=item.sold_quantity,
            remaining_quantity=item.remaining_quantity,
        )
        for item in items
    )



def _build_platform_breakdown(*, sales: list[FarmDropSale]) -> tuple[PlatformBreakdownRow, ...]:
    rows: list[PlatformBreakdownRow] = []
    for platform_code, label in FarmDropSale.Platform.choices:
        platform_sales = [sale for sale in sales if sale.platform == platform_code]
        if not platform_sales:
            continue
        rows.append(
            PlatformBreakdownRow(
                platform=platform_code,
                platform_label=label,
                sales_count=len(platform_sales),
                gross_sale_rub=q_money(sum((sale.gross_sale_rub for sale in platform_sales), ZERO_MONEY)),
                net_sale_rub=q_money(sum((sale.net_sale_rub for sale in platform_sales), ZERO_MONEY)),
                profit_rub=q_money(sum((sale.profit_rub for sale in platform_sales), ZERO_MONEY)),
                commission_saved_rub=q_money(sum((sale.commission_saved_rub for sale in platform_sales), ZERO_MONEY)),
            )
        )
    return tuple(rows)



def get_caseup_turnover_rub(user) -> Decimal:
    value = (
        FarmDropSale.objects.filter(owner=user, platform=FarmDropSale.Platform.CASEUP)
        .aggregate(total=Sum("gross_sale_rub"))
        .get("total")
    )
    return q_money(value)



def grant_farmer_rewards_for_user(user) -> list[FarmerRewardGrant]:
    if not user_has_feature(user, FARM_BONUS_PROGRAM, family="farmer"):
        return []

    turnover = get_caseup_turnover_rub(user)
    tier_ids = set(
        FarmerRewardGrant.objects.filter(owner=user).values_list("tier_id", flat=True)
    )
    created: list[FarmerRewardGrant] = []
    for tier in FarmerRewardTier.objects.filter(is_active=True).order_by("sort_order", "threshold_turnover_rub", "id"):
        if tier.id in tier_ids:
            continue
        if turnover < q_money(tier.threshold_turnover_rub):
            continue
        grant = FarmerRewardGrant.objects.create(
            owner=user,
            tier=tier,
            turnover_rub_snapshot=turnover,
            notes=f"Auto-granted from CaseUP turnover {turnover:.2f} ₽",
        )
        message = (
            "🏁 Farmer milestone reached\n\n"
            f"Тир: {tier.title}\n"
            f"Порог: {tier.threshold_turnover_rub:.2f} ₽\n"
            f"Ваш оборот CaseUP: {turnover:.2f} ₽\n"
            f"Бонус: {tier.reward_title or tier.title}"
        )
        sent = _maybe_send_user_message(user=user, text=message)
        _create_notification_event(
            owner=user,
            event_type=FarmNotificationEvent.EventType.MILESTONE,
            payload={"sent": sent, "tier": tier.code, "turnover_rub": f"{turnover:.2f}"},
        )
        created.append(grant)
    return created



def get_reward_progress(user) -> FarmerRewardProgress:
    turnover = get_caseup_turnover_rub(user)
    tiers = list(FarmerRewardTier.objects.filter(is_active=True).order_by("sort_order", "threshold_turnover_rub", "id"))
    grants = tuple(FarmerRewardGrant.objects.filter(owner=user).select_related("tier").order_by("-granted_at", "-id"))
    current_tier = None
    next_tier = None
    for tier in tiers:
        if turnover >= q_money(tier.threshold_turnover_rub):
            current_tier = tier
            continue
        next_tier = tier
        break
    remaining = q_money(next_tier.threshold_turnover_rub - turnover) if next_tier else ZERO_MONEY
    return FarmerRewardProgress(
        turnover_rub=turnover,
        current_tier=current_tier,
        next_tier=next_tier,
        remaining_to_next_rub=remaining,
        grants=grants,
    )



def get_dashboard_analytics_for_user(user) -> FarmDashboardAnalytics:
    sessions = list(
        FarmDropSession.objects.filter(owner=user)
        .select_related("account")
        .prefetch_related("items__steam_item", "items__sales", "sales")
        .order_by("-collected_at", "-id")
    )
    session_rows = tuple(calculate_session_analytics(session) for session in sessions)
    item_rows = [row for session_row in session_rows for row in session_row.item_analytics]
    sales = list(FarmDropSale.objects.filter(owner=user).select_related("item", "session").order_by("-sold_at", "-id"))

    collected = q_money(sum((row.collected_value_rub for row in session_rows), ZERO_MONEY))
    current = q_money(sum((row.current_value_rub for row in session_rows), ZERO_MONEY))
    delta = q_money(current - collected)
    delta_percent = q_percent((delta / collected) * ONE_HUNDRED) if collected > ZERO_MONEY else Decimal("0.00")
    realized = q_money(sum((row.realized_pnl_rub for row in session_rows), ZERO_MONEY))
    unrealized = q_money(sum((row.unrealized_pnl_rub for row in session_rows), ZERO_MONEY))
    total = q_money(realized + unrealized)
    platform_breakdown = _build_platform_breakdown(sales=sales)
    caseup_turnover = q_money(sum((row.gross_sale_rub for row in platform_breakdown if row.platform == FarmDropSale.Platform.CASEUP), ZERO_MONEY))
    commission_saved = q_money(sum((row.commission_saved_rub for row in platform_breakdown), ZERO_MONEY))

    best_positions = tuple(sorted(item_rows, key=lambda row: (row.total_pnl_rub, row.unit_delta_percent), reverse=True)[:5])
    worst_positions = tuple(sorted(item_rows, key=lambda row: (row.total_pnl_rub, row.unit_delta_percent))[:5])
    open_positions_count = sum(1 for row in item_rows if row.remaining_quantity > 0)
    sold_positions_count = sum(1 for row in item_rows if row.sold_quantity > 0)

    return FarmDashboardAnalytics(
        collected_value_rub=collected,
        current_value_rub=current,
        delta_rub=delta,
        delta_percent=delta_percent,
        realized_pnl_rub=realized,
        unrealized_pnl_rub=unrealized,
        total_pnl_rub=total,
        sold_positions_count=sold_positions_count,
        open_positions_count=open_positions_count,
        caseup_turnover_rub=caseup_turnover,
        commission_saved_rub=commission_saved,
        session_analytics=session_rows,
        best_positions=best_positions,
        worst_positions=worst_positions,
        platform_breakdown=platform_breakdown,
        reward_progress=get_reward_progress(user),
    )



def create_drop_session(*, account: FarmAccount, collected_at, source_type: str, notes: str, created_by, title: str = "", items_payload: list[dict]) -> FarmDropSession:
    with transaction.atomic():
        session = FarmDropSession.objects.create(
            owner=account.owner,
            account=account,
            source_type=source_type,
            title=(title or "").strip(),
            collected_at=collected_at,
            notes=(notes or "").strip(),
            created_by=created_by,
        )
        item_rows: list[FarmDropItem] = []
        for payload in items_payload:
            quantity = int(payload.get("quantity") or 0)
            if quantity <= 0:
                continue
            steam_item = payload.get("steam_item")
            market_hash_name = (payload.get("market_hash_name") or getattr(steam_item, "market_hash_name", "") or "").strip()
            if not market_hash_name:
                continue
            item_row = FarmDropItem(
                session=session,
                owner=account.owner,
                steam_item=steam_item,
                name_snapshot=(payload.get("name_snapshot") or getattr(steam_item, "name", "") or market_hash_name).strip(),
                market_hash_name_snapshot=market_hash_name,
                icon_url_snapshot=(payload.get("icon_url_snapshot") or getattr(steam_item, "icon_url", "") or "").strip(),
                quantity=quantity,
                remaining_quantity=quantity,
                collected_price_rub_snapshot=q_money(payload.get("collected_price_rub_snapshot") or getattr(steam_item, "price_rub", ZERO_MONEY)),
                notes=(payload.get("notes") or "").strip(),
            )
            item_row.full_clean()
            item_rows.append(item_row)

        if not item_rows:
            raise ValueError("Farm drop session must contain at least one valid item.")

        FarmDropItem.objects.bulk_create(item_rows)
        return session



def resolve_item_status(item: FarmDropItem) -> str:
    if item.remaining_quantity <= 0:
        return FarmDropItem.Status.SOLD
    if item.sold_quantity > 0:
        return FarmDropItem.Status.PARTIAL
    return FarmDropItem.Status.ACTIVE



def record_drop_sale(*, item: FarmDropItem, quantity: int, platform: str, gross_sale_rub, sold_at, notes: str = "") -> FarmDropSale:
    if quantity <= 0:
        raise ValueError("Quantity must be > 0.")

    with transaction.atomic():
        locked_item = (
            FarmDropItem.objects
            .select_for_update()
            .select_related("session", "owner")
            .get(pk=item.pk)
        )

        if quantity > locked_item.remaining_quantity:
            raise ValueError("Нельзя продать больше, чем осталось в позиции.")

        fee_percent = resolve_sale_fee_percent(user=locked_item.owner, platform=platform)
        gross_sale_rub = q_money(gross_sale_rub)
        fee_rub = q_money(gross_sale_rub * (fee_percent / ONE_HUNDRED))
        net_sale_rub = q_money(gross_sale_rub - fee_rub)
        cost_basis_rub = q_money(locked_item.collected_price_rub_snapshot * quantity)
        profit_rub = q_money(net_sale_rub - cost_basis_rub)
        profit_percent = q_percent((profit_rub / cost_basis_rub) * ONE_HUNDRED) if cost_basis_rub > ZERO_MONEY else Decimal("0.00")

        commission_saved = ZERO_MONEY
        if platform == FarmDropSale.Platform.CASEUP:
            config = get_farm_module_config()
            default_fee_rub = q_money(gross_sale_rub * (q_percent(config.default_caseup_fee_percent) / ONE_HUNDRED))
            commission_saved = q_money(default_fee_rub - fee_rub)

        sale = FarmDropSale.objects.create(
            owner=locked_item.owner,
            session=locked_item.session,
            item=locked_item,
            platform=platform,
            sold_at=sold_at,
            quantity=quantity,
            gross_sale_rub=gross_sale_rub,
            fee_percent_snapshot=fee_percent,
            fee_rub=fee_rub,
            net_sale_rub=net_sale_rub,
            cost_basis_rub=cost_basis_rub,
            profit_rub=profit_rub,
            profit_percent=profit_percent,
            commission_saved_rub=commission_saved,
            notes=(notes or "").strip(),
        )

        locked_item.remaining_quantity -= quantity
        locked_item.status = resolve_item_status(locked_item)
        locked_item.save(update_fields=["remaining_quantity", "status", "updated_at"])

        transaction.on_commit(lambda: notify_sale_recorded(sale_id=sale.id))
        transaction.on_commit(lambda: grant_farmer_rewards_for_user(locked_item.owner))
        return sale



def build_farm_item_absolute_url(item: FarmDropItem) -> str:
    base_url = (get_public_base_url() or "").rstrip("/")
    path = reverse("farm:sessions_detail", args=[item.session_id])
    return f"{base_url}{path}#item-{item.id}" if base_url else path



def _bot_link(url: str) -> str:
    from modules.v2.tg_notifier.services import bot_utm_link

    return bot_utm_link(url, content="farm")



def build_caseup_advantage(item: FarmDropItem) -> FarmCaseupAdvantage:
    config = get_farm_module_config()
    current_price = current_item_price_rub(item)
    caseup_fee = resolve_sale_fee_percent(user=item.owner, platform=FarmDropSale.Platform.CASEUP)
    caseup_net = q_money(current_price * (ONE_HUNDRED - caseup_fee) / ONE_HUNDRED)
    external_net = q_money(current_price * (ONE_HUNDRED - q_percent(config.external_reference_fee_percent)) / ONE_HUNDRED)
    return FarmCaseupAdvantage(
        item=item,
        current_price_rub=current_price,
        external_net_rub=external_net,
        caseup_net_rub=caseup_net,
        advantage_rub=q_money(caseup_net - external_net),
    )



def _create_notification_event(*, owner, event_type: str, payload: dict, item: FarmDropItem | None = None, sale: FarmDropSale | None = None) -> FarmNotificationEvent:
    return FarmNotificationEvent.objects.create(
        owner=owner,
        item=item,
        sale=sale,
        event_type=event_type,
        payload=payload,
    )



def _notify_on_site(user, text: str) -> None:
    """Farm alerts land in the notification center (and on the user's devices as pushes)."""
    from django.urls import reverse

    from modules.v2.farm.notifications import KIND_FARM_ALERT
    from modules.v2.notifications.services import notify

    title, _sep, body = text.strip().partition("\n")
    try:
        notify(user, KIND_FARM_ALERT, dedupe=False, payload={"title": title.strip()[:200], "body": " · ".join(line.strip() for line in body.splitlines() if line.strip())[:600], "url": reverse("farm:dashboard")})
    except Exception:  # noqa: BLE001 - a notification must not break farm bookkeeping
        pass


def _maybe_send_user_message(*, user, text: str) -> bool:
    """Returns whether the Telegram copy was sent; the site notification goes out either way."""
    if not user_has_feature(user, FARM_TG_ALERTS, family="farmer"):
        return False
    _notify_on_site(user, text)
    if not is_module_enabled("tg_notifier"):
        return False
    try:
        send_user_message(user=user, text=text, allow_default=False)
        return True
    except Exception:
        return False



def notify_sale_recorded(*, sale_id: int) -> FarmNotificationEvent | None:
    sale = FarmDropSale.objects.select_related("item", "session", "owner").get(pk=sale_id)
    message = (
        "💸 Farm sale recorded\n\n"
        f"Предмет: {sale.item.market_hash_name_snapshot}\n"
        f"Площадка: {sale.get_platform_display()}\n"
        f"Количество: {sale.quantity}\n"
        f"Сумма после комиссии: {sale.net_sale_rub:.2f} ₽\n"
        f"PnL: {sale.profit_rub:+.2f} ₽ ({sale.profit_percent:+.2f}%)\n"
        f"Сессия: {sale.session.collected_at:%d.%m.%Y %H:%M}"
    )
    sent = _maybe_send_user_message(user=sale.owner, text=message)
    payload = {
        "sent": sent,
        "net_sale_rub": f"{sale.net_sale_rub:.2f}",
        "profit_rub": f"{sale.profit_rub:.2f}",
        "platform": sale.platform,
    }
    return _create_notification_event(owner=sale.owner, item=sale.item, sale=sale, event_type=FarmNotificationEvent.EventType.SALE, payload=payload)



def dispatch_price_threshold_notifications(*, user=None) -> list[FarmNotificationEvent]:
    config = get_farm_module_config()
    items_qs = FarmDropItem.objects.select_related("owner", "steam_item", "session").filter(remaining_quantity__gt=0)
    if user is not None:
        items_qs = items_qs.filter(owner=user)

    events: list[FarmNotificationEvent] = []
    for item in items_qs:
        current_price = current_item_price_rub(item)
        collected = q_money(item.collected_price_rub_snapshot)
        if collected <= ZERO_MONEY:
            continue
        delta_percent = q_percent(((current_price - collected) / collected) * ONE_HUNDRED)
        event_type = None
        if delta_percent >= q_percent(config.price_up_threshold_percent):
            event_type = FarmNotificationEvent.EventType.PRICE_UP
            label = "📈 Рост цены farmer drop"
        elif delta_percent <= -q_percent(config.price_down_threshold_percent):
            event_type = FarmNotificationEvent.EventType.PRICE_DOWN
            label = "📉 Падение цены farmer drop"
        else:
            continue

        existing = FarmNotificationEvent.objects.filter(
            owner=item.owner,
            item=item,
            event_type=event_type,
        ).exists()
        if existing:
            continue

        message = (
            f"{label}\n\n"
            f"Предмет: {item.market_hash_name_snapshot}\n"
            f"Цена на сборе: {collected:.2f} ₽\n"
            f"Текущая цена: {current_price:.2f} ₽\n"
            f"Изменение: {delta_percent:+.2f}%\n"
            f"Открыть: {_bot_link(build_farm_item_absolute_url(item))}"
        )
        sent = _maybe_send_user_message(user=item.owner, text=message)
        event = _create_notification_event(
            owner=item.owner,
            item=item,
            event_type=event_type,
            payload={"sent": sent, "delta_percent": f"{delta_percent:.2f}", "current_price_rub": f"{current_price:.2f}"},
        )
        events.append(event)
    return events



def dispatch_caseup_advantage_notifications(*, user=None) -> list[FarmNotificationEvent]:
    config = get_farm_module_config()
    items_qs = FarmDropItem.objects.select_related("owner", "steam_item", "session").filter(remaining_quantity__gt=0)
    if user is not None:
        items_qs = items_qs.filter(owner=user)
    events: list[FarmNotificationEvent] = []
    for item in items_qs:
        advantage = build_caseup_advantage(item)
        if advantage.advantage_rub < q_money(config.caseup_advantage_min_rub):
            continue
        existing = FarmNotificationEvent.objects.filter(
            owner=item.owner,
            item=item,
            event_type=FarmNotificationEvent.EventType.CASEUP_ADVANTAGE,
        ).exists()
        if existing:
            continue
        message = (
            "🏷️ Выгоднее продать на CaseUP\n\n"
            f"Предмет: {item.market_hash_name_snapshot}\n"
            f"CaseUP net: {advantage.caseup_net_rub:.2f} ₽\n"
            f"External ref net: {advantage.external_net_rub:.2f} ₽\n"
            f"Разница: +{advantage.advantage_rub:.2f} ₽\n"
            f"Открыть: {_bot_link(build_farm_item_absolute_url(item))}"
        )
        sent = _maybe_send_user_message(user=item.owner, text=message)
        event = _create_notification_event(
            owner=item.owner,
            item=item,
            event_type=FarmNotificationEvent.EventType.CASEUP_ADVANTAGE,
            payload={"sent": sent, "advantage_rub": f"{advantage.advantage_rub:.2f}"},
        )
        events.append(event)
    return events
