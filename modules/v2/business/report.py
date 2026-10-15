"""Morning e-mail with yesterday's numbers, sent to ADMIN_EMAILS."""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from .models import RevenueCategory
from .stats import CATEGORY_ORDER, change_percent, commission_breakdown, day_period, money_summary

logger = logging.getLogger(__name__)
CATEGORY_LABELS = dict(RevenueCategory.choices)


def _site_url() -> str:
    for origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []:
        if origin.startswith("https://") and "*" not in origin:
            return origin.rstrip("/")
    domain = os.getenv("CADDY_DOMAIN", "").strip()
    return f"https://{domain}" if domain else ""


def _rub(value) -> str:
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


def _delta(current, previous) -> str:
    percent = change_percent(current, previous)
    if percent is None:
        return ""
    return f" ({'+' if percent > 0 else ''}{percent}% к позавчера)"


def build_daily_report(*, day: date | None = None) -> tuple[str, str, str]:
    """Returns (subject, plain text, html) for one calendar day (yesterday by default)."""
    day = day or timezone.localdate() - timedelta(days=1)
    period = day_period(day)
    money = money_summary(period.start, period.end)
    previous = money_summary(period.prev_start, period.prev_end)
    commissions = [row for row in commission_breakdown(money) if row["count"]]

    lines = [
        ("Пополнения", f"{_rub(money['topups'])} · {money['topups_count']} платежей · {money['payers']} плательщиков{_delta(money['topups'], previous['topups'])}"),
        ("Доход площадки", f"{_rub(money['revenue'])}{_delta(money['revenue'], previous['revenue'])}"),
    ]
    lines += [(f"  {CATEGORY_LABELS[code]}", _rub(money["categories"][code])) for code in CATEGORY_ORDER]
    lines += [
        ("Расходы на бонусы", _rub(money["bonus_total"])),
        ("Чистый доход", f"{_rub(money['net'])}{_delta(money['net'], previous['net'])}"),
        ("Регистрации", f"{money['registrations']}{_delta(money['registrations'], previous['registrations'])}"),
    ]

    subject = f"Сводка за {day:%d.%m.%Y}: доход {_rub(money['revenue'])}, комиссии {_rub(money['commission'])}"
    url = _site_url()
    panel = f"{url}{reverse('control_center:business')}?period=custom&from={day:%Y-%m-%d}&to={day:%Y-%m-%d}" if url else ""

    text = [f"CaseUP — итоги дня {day:%d.%m.%Y}", ""]
    text += [f"{label}: {value}" for label, value in lines]
    text += ["", "Комиссии по видам:"]
    text += [f"  {row['label']}: {_rub(row['total'])} · {row['count']} опер." for row in commissions] or ["  за день комиссий не было"]
    if panel:
        text += ["", f"Бизнес-панель: {panel}"]

    rows_html = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#666">{escape(label.strip())}</td><td style="padding:4px 0"><b>{escape(value)}</b></td></tr>'
        for label, value in lines
    )
    commission_html = "".join(f"<li>{escape(row['label'])}: <b>{escape(_rub(row['total']))}</b> · {row['count']} опер.</li>" for row in commissions) or "<li>за день комиссий не было</li>"
    link_html = f'<p><a href="{escape(panel)}">Открыть бизнес-панель</a></p>' if panel else ""
    html = (
        f'<div style="font-family:Arial,sans-serif;font-size:14px">'
        f"<h2 style=\"margin:0 0 12px\">CaseUP — итоги дня {day:%d.%m.%Y}</h2>"
        f"<table>{rows_html}</table>"
        f"<h3 style=\"margin:16px 0 6px\">Комиссии по видам</h3><ul>{commission_html}</ul>{link_html}</div>"
    )
    return subject, "\n".join(text), html


def send_daily_report(*, day: date | None = None) -> bool:
    if not getattr(settings, "BUSINESS_DAILY_REPORT", True) or not settings.ADMINS:
        return False
    from django.core.mail import mail_admins

    try:
        subject, text, html = build_daily_report(day=day)
        mail_admins(subject, text, html_message=html)
    except Exception:  # noqa: BLE001
        logger.exception("Daily business report failed")
        return False
    return True
