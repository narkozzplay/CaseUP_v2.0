"""Paying for the server: balance of the cloud, how long it lasts, when to top it up and by how much.

- The cloud (Рег.облако) bills by the hour from a prepaid balance. The balance is either typed in / topped up
  by hand in Control Center → «Сервер», or synced hourly from the provider API when REGRU_CLOUDVPS_TOKEN is set.
  Between updates the balance is estimated: the last known value minus the hourly cost since then.
- The site shows the balance minus `reserve_days` of cost (owner's request 30.10.2026): the date to top up by
  is when that reserve would start being spent, so a late payment still leaves a week before the server stops.
- Shared hosting / domains are renewed by date (HostingItem.paid_until).
- system.hosting_check (hourly) syncs the API and sends the admin alerts (Telegram team chat + push), at most
  once a day per subject.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import HOURS_PER_MONTH, HostingItem, HostingSettings, HostingTopUp

logger = logging.getLogger(__name__)

ALERT_REPEAT_SECONDS = 24 * 3600
API_TIMEOUT = 15
URL = "/control-center/server/"
CENT = Decimal("0.01")


class HostingError(ValueError):
    pass


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT)


def money_label(value) -> str:
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


@dataclass
class Forecast:
    hourly_cost: Decimal
    monthly_cost: Decimal
    cost_source: str
    balance: Decimal | None = None
    balance_at: datetime | None = None
    reserve_days: int = 7
    reserve_rub: Decimal = Decimal("0")
    available: Decimal | None = None
    runs_out_at: datetime | None = None
    renew_by: datetime | None = None
    days_to_renew: float | None = None
    days_left: float | None = None
    topup_amount: Decimal = Decimal("0")
    topup_months: int = 1
    items_monthly: Decimal = Decimal("0")
    api_monthly: Decimal | None = None
    state: str = "info"
    items: list = field(default_factory=list)

    @property
    def next_topup_amount(self) -> Decimal:
        """On the top-up date the reserve is still there, so the payment covers only the months."""
        return _money(self.monthly_cost * self.topup_months)

    @property
    def has_balance(self) -> bool:
        return self.balance is not None


def api_token() -> str:
    return (getattr(settings, "REGRU_CLOUDVPS_TOKEN", "") or "").strip()


def balance_items():
    return HostingItem.objects.filter(is_active=True, billing=HostingItem.Billing.BALANCE)


def api_cost(config: HostingSettings) -> Decimal | None:
    """The provider's own hourly total, or the sum of its resources when it did not send one."""
    if config.balance_source != HostingSettings.Source.API:
        return None
    if config.api_hourly_cost:
        return Decimal(config.api_hourly_cost)
    try:
        total = sum((Decimal(str(row.get("price") or 0)) for row in config.api_resources or []), Decimal("0"))
    except InvalidOperation:
        return None
    return total or None


def items_cost() -> Decimal:
    return sum((item.hourly_price for item in balance_items()), Decimal("0"))


def hourly_cost(config: HostingSettings) -> tuple[Decimal, str]:
    """A cost typed in by hand wins; otherwise the larger of the list and the API. The API may leave something
    out (the floating IP was missing from its resources, 30.10.2026), and a forecast that is too long is the
    one that lets the server stop."""
    if config.monthly_cost_rub:
        return Decimal(config.monthly_cost_rub) / HOURS_PER_MONTH, "manual"
    items = items_cost()
    api = api_cost(config)
    if api and api >= items:
        return api, "api"
    return items, "items"


def build_forecast(config: HostingSettings | None = None, *, now: datetime | None = None) -> Forecast:
    config = config or HostingSettings.load()
    now = now or timezone.now()
    hourly, source = hourly_cost(config)
    forecast = Forecast(
        hourly_cost=hourly,
        monthly_cost=_money(hourly * HOURS_PER_MONTH),
        cost_source=source,
        reserve_days=config.reserve_days,
        reserve_rub=_money(hourly * 24 * config.reserve_days),
        topup_months=max(1, config.topup_months),
        items_monthly=_money(items_cost() * HOURS_PER_MONTH),
    )
    api = api_cost(config)
    if api:
        forecast.api_monthly = _money(api * HOURS_PER_MONTH)
    if config.balance_rub is None:
        return forecast
    known = Decimal(config.balance_rub) + Decimal(config.bonus_rub or 0)
    since = config.balance_at or now
    spent_hours = Decimal(max(0.0, (now - since).total_seconds()) / 3600)
    balance = max(Decimal("0"), known - hourly * spent_hours)
    forecast.balance = _money(balance)
    forecast.balance_at = config.balance_at
    forecast.available = _money(balance - forecast.reserve_rub)
    if hourly > 0:
        hours_left = float(balance / hourly)
        forecast.days_left = hours_left / 24
        forecast.runs_out_at = now + timedelta(hours=hours_left)
        forecast.renew_by = forecast.runs_out_at - timedelta(days=config.reserve_days)
        forecast.days_to_renew = (forecast.renew_by - now).total_seconds() / 86400
        # Enough for topup_months of work after the top-up, the reserve kept on top.
        wanted = hourly * HOURS_PER_MONTH * forecast.topup_months + forecast.reserve_rub
        forecast.topup_amount = _money(max(Decimal("0"), wanted - balance))
        if forecast.days_to_renew <= 0:
            forecast.state = "fail"
        elif forecast.days_to_renew <= config.balance_warn_days:
            forecast.state = "warn"
        else:
            forecast.state = "ok"
    return forecast


def projection_after_topup(amount: Decimal, config: HostingSettings | None = None) -> Forecast:
    """The forecast as if `amount` was added now (the hint next to the «Пополнено» button)."""
    config = config or HostingSettings.load()
    current = build_forecast(config)
    preview = HostingSettings(**{f.attname: getattr(config, f.attname) for f in HostingSettings._meta.concrete_fields})
    preview.balance_rub = (current.balance or Decimal("0")) + amount
    preview.bonus_rub = Decimal("0")
    preview.balance_at = timezone.now()
    return build_forecast(preview)


# --------------------------------------------------------------------------- renewals


@dataclass
class Renewal:
    item: HostingItem
    days_left: int | None
    state: str


def renewals(config: HostingSettings | None = None, *, today: date | None = None) -> list[Renewal]:
    config = config or HostingSettings.load()
    today = today or timezone.localdate()
    rows = []
    for item in HostingItem.objects.filter(is_active=True, billing=HostingItem.Billing.RENEWAL).order_by("paid_until", "name"):
        if item.paid_until is None:
            rows.append(Renewal(item, None, "info"))
            continue
        days = (item.paid_until - today).days
        state = "fail" if days < 0 else ("warn" if days <= config.renewal_warn_days else "ok")
        rows.append(Renewal(item, days, state))
    return rows


# --------------------------------------------------------------------------- changes from Control Center


def _decimal(raw, *, label: str, allow_empty: bool = False) -> Decimal | None:
    text = str(raw or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        if allow_empty:
            return None
        raise HostingError(f"Укажите {label}.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise HostingError(f"{label.capitalize()}: нужно число.") from None
    if value < 0 or value > Decimal("10000000"):
        raise HostingError(f"{label.capitalize()}: число от 0 до 10 000 000.")
    return value


def _int(raw, *, label: str, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HostingError(f"{label}: нужно целое число.") from None
    if not low <= value <= high:
        raise HostingError(f"{label}: от {low} до {high}.")
    return value


def record_topup(amount_raw, *, user=None) -> tuple[HostingTopUp, Forecast]:
    amount = _decimal(amount_raw, label="сумму пополнения")
    if amount <= 0:
        raise HostingError("Сумма пополнения должна быть больше нуля.")
    with transaction.atomic():
        config = HostingSettings.objects.select_for_update().get(pk=HostingSettings.load().pk)
        current = build_forecast(config).balance or Decimal("0")
        config.balance_rub = _money(current + amount)
        config.bonus_rub = Decimal("0")
        config.balance_at = timezone.now()
        config.save(update_fields=["balance_rub", "bonus_rub", "balance_at", "updated_at"])
        topup = HostingTopUp.objects.create(amount_rub=_money(amount), balance_after_rub=config.balance_rub, created_by=user)
    return topup, build_forecast(config)


def set_balance(amount_raw) -> HostingSettings:
    config = HostingSettings.load()
    config.balance_rub = _money(_decimal(amount_raw, label="баланс"))
    config.bonus_rub = Decimal("0")
    config.balance_at = timezone.now()
    config.balance_source = HostingSettings.Source.MANUAL
    config.save(update_fields=["balance_rub", "bonus_rub", "balance_at", "balance_source", "updated_at"])
    return config


def save_settings(form) -> list[str]:
    config = HostingSettings.load()
    changes = []
    values = {
        "provider_name": (str(form.get("provider_name") or "").strip()[:80] or config.provider_name),
        "panel_url": str(form.get("panel_url") or "").strip()[:200],
        "reserve_days": _int(form.get("reserve_days"), label="Запас", low=0, high=60),
        "balance_warn_days": _int(form.get("balance_warn_days"), label="Предупреждение о балансе", low=0, high=60),
        "renewal_warn_days": _int(form.get("renewal_warn_days"), label="Предупреждение о продлении", low=0, high=120),
        "topup_months": _int(form.get("topup_months"), label="Пополнять на месяцев", low=1, high=24),
        "ram_warn_percent": _int(form.get("ram_warn_percent"), label="Порог памяти", low=50, high=100),
        "monthly_cost_rub": _decimal(form.get("monthly_cost_rub"), label="расход в месяц", allow_empty=True),
        "alerts_enabled": bool(form.get("alerts_enabled")),
    }
    if values["panel_url"] and not values["panel_url"].startswith(("https://", "http://")):
        raise HostingError("Ссылка на панель должна начинаться с https://")
    for name, value in values.items():
        if getattr(config, name) != value:
            changes.append(f"{HostingSettings._meta.get_field(name).verbose_name}: {getattr(config, name)} → {value}")
            setattr(config, name, value)
    if changes:
        config.save()
    return changes


def save_item(form, *, item: HostingItem | None = None) -> HostingItem:
    item = item or HostingItem()
    name = str(form.get("name") or "").strip()[:120]
    if not name:
        raise HostingError("Укажите название услуги.")
    kind = form.get("kind") or HostingItem.Kind.OTHER
    billing = form.get("billing") or HostingItem.Billing.BALANCE
    period = form.get("period") or HostingItem.Period.HOUR
    if kind not in HostingItem.Kind.values or billing not in HostingItem.Billing.values or period not in HostingItem.Period.values:
        raise HostingError("Неизвестный тип, оплата или период.")
    paid_until = None
    raw_date = str(form.get("paid_until") or "").strip()
    if raw_date:
        try:
            paid_until = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            raise HostingError("Дата «Оплачено до» в формате ГГГГ-ММ-ДД.") from None
    if billing == HostingItem.Billing.RENEWAL and paid_until is None:
        raise HostingError("Для услуги с продлением укажите, до какой даты она оплачена.")
    item.name = name
    item.kind = kind
    item.billing = billing
    item.plan = str(form.get("plan") or "").strip()[:120]
    item.price_rub = _decimal(form.get("price_rub"), label="цену")
    item.period = period
    item.paid_until = paid_until
    item.note = str(form.get("note") or "").strip()[:200]
    item.is_active = True
    item.save()
    return item


# --------------------------------------------------------------------------- provider API (Рег.облако)


def sync_from_api(config: HostingSettings | None = None) -> bool:
    """Balance and per-resource prices from GET /v1/balance_data (developers.cloudvps.reg.ru/billing/balance.html).
    Returns True on success; the error is kept in api_error and the manual values stay."""
    token = api_token()
    config = config or HostingSettings.load()
    if not token:
        return False
    base = (getattr(settings, "REGRU_CLOUDVPS_API_URL", "") or "https://api.cloudvps.reg.ru/v1").rstrip("/")
    try:
        response = requests.get(
            f"{base}/balance_data",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=API_TIMEOUT,
        )
        if response.status_code == 401:
            raise HostingError("API: ключ не подошёл (401). Проверьте REGRU_CLOUDVPS_TOKEN.")
        response.raise_for_status()
        data = response.json().get("balance_data") or {}
        balance = Decimal(str(data["balance"]))
        bonus = Decimal(str(data.get("bonus_balance") or 0))
    except (requests.RequestException, ValueError, KeyError, InvalidOperation, HostingError) as exc:
        message = str(exc) if isinstance(exc, HostingError) else f"API: {exc.__class__.__name__}: {str(exc)[:200]}"
        config.api_error = message[:300]
        config.save(update_fields=["api_error", "updated_at"])
        logger.warning("hosting api sync failed: %s", message)
        return False
    resources = []

    def collect(rows, parent: str = "") -> None:
        # Child services (backups, IPs) come in `linked` of their server.
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("plan") or row.get("type") or "")
            resources.append(
                {
                    "name": (f"{parent} → {name}" if parent else name)[:120],
                    "type": str(row.get("type") or "")[:20],
                    "plan": str(row.get("plan") or "")[:60],
                    "state": str(row.get("state") or "")[:20],
                    "price": str(row.get("price") or "0"),
                    "price_month": str(row.get("price_month") or "0"),
                }
            )
            linked = row.get("linked")
            collect(linked if isinstance(linked, list) else [], name)

    collect(data.get("detalization"))
    try:
        api_hourly = Decimal(str(data["hourly_cost"])) if data.get("hourly_cost") not in (None, "") else None
    except InvalidOperation:
        api_hourly = None
    config.balance_rub = _money(balance)
    config.bonus_rub = _money(bonus)
    config.balance_at = timezone.now()
    config.balance_source = HostingSettings.Source.API
    config.api_synced_at = config.balance_at
    config.api_error = ""
    config.api_resources = resources
    config.api_hourly_cost = api_hourly
    config.save()
    return True


# --------------------------------------------------------------------------- alerts


def _date_label(moment: datetime) -> str:
    return timezone.localtime(moment).strftime("%d.%m.%Y %H:%M")


def check_balance(config: HostingSettings, forecast: Forecast) -> str:
    from .alerts import send_admin_alert

    if not forecast.has_balance or forecast.renew_by is None or forecast.state not in {"warn", "fail"}:
        return ""
    if forecast.state == "fail":
        title = f"Баланс {config.provider_name}: пошёл запас"
        lead = f"Пополнить нужно было до {_date_label(forecast.renew_by)}."
    else:
        days = max(0, int(forecast.days_to_renew))
        title = f"Баланс {config.provider_name}: пополнить до {timezone.localtime(forecast.renew_by):%d.%m}"
        lead = f"Осталось {days} дн. до даты пополнения ({_date_label(forecast.renew_by)})."
    text = (
        f"{lead}\nБаланс ≈ {money_label(forecast.balance)}, расход {money_label(forecast.monthly_cost)}/мес. "
        f"Деньги закончатся {_date_label(forecast.runs_out_at)} — сервер остановится.\n"
        f"Пополните на {money_label(forecast.topup_amount)} (хватит на {forecast.topup_months} мес. + {forecast.reserve_days} дн. запаса)."
    )
    if config.panel_url:
        text += f"\nПанель: {config.panel_url}"
    sent = send_admin_alert("hosting:balance", title, text, url=URL, repeat_after=ALERT_REPEAT_SECONDS)
    return "alert" if sent else ""


def check_renewals(config: HostingSettings) -> dict[str, str]:
    from .alerts import send_admin_alert

    results = {}
    for row in renewals(config):
        if row.state not in {"warn", "fail"}:
            continue
        item = row.item
        when = item.paid_until.strftime("%d.%m.%Y")
        title = f"Продлить: {item.name}" + (" — срок прошёл" if row.days_left < 0 else f" через {row.days_left} дн.")
        text = f"{item.get_kind_display()} оплачен(а) до {when}. Продление {money_label(item.price_rub)} {item.get_period_display()}."
        if item.note:
            text += f"\n{item.note}"
        if send_admin_alert(f"hosting:renewal:{item.pk}", title, text, url=URL, repeat_after=ALERT_REPEAT_SECONDS):
            results[str(item.pk)] = "alert"
    return results


def run_hosting_check() -> dict:
    config = HostingSettings.load()
    report = {"api": None}
    if api_token():
        report["api"] = sync_from_api(config)
        config.refresh_from_db()
    if config.alerts_enabled:
        report["balance"] = check_balance(config, build_forecast(config))
        report["renewals"] = check_renewals(config)
    return report
