"""Control Center → «Сервер»: server load and paying for the server (modules/v2/system/server_stats.py, hosting.py)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from modules.v2.system import hosting, server_stats
from modules.v2.system.models import HostingItem, HostingSettings, HostingTopUp, ServerMetricSnapshot

from .models import AuditAction
from .services.audit import log_admin_action
from .services.health import format_bytes
from .views import _admin_access, _forbidden, _render


def _state(percent: float, warn: float, fail: float) -> str:
    return "fail" if percent >= fail else ("warn" if percent >= warn else "ok")


def _now_cards(reading, config) -> list[dict]:
    mem_used = reading.mem_total - reading.mem_available
    mem_pct = mem_used * 100 / reading.mem_total if reading.mem_total else 0
    swap_pct = reading.swap_used * 100 / reading.swap_total if reading.swap_total else 0
    disk_pct = reading.disk_used * 100 / reading.disk_total if reading.disk_total else 0
    load_per_cpu = reading.load5 / max(1, reading.cpu_count)
    return [
        {
            "label": "Процессор",
            "value": f"{reading.cpu_percent:.0f}%" if reading.cpu_percent is not None else "—",
            "hint": f"load {reading.load1:.2f} / {reading.load5:.2f} / {reading.load15:.2f} · ядер {reading.cpu_count}",
            "state": "fail" if load_per_cpu >= server_stats.LOAD_WARN_PER_CPU else ("warn" if load_per_cpu >= 1 else "ok"),
        },
        {
            "label": "Память",
            "value": f"{mem_pct:.0f}%",
            "hint": f"{format_bytes(mem_used)} из {format_bytes(reading.mem_total)} · свободно {format_bytes(reading.mem_available)}",
            "state": _state(mem_pct, config.ram_warn_percent - 10, config.ram_warn_percent),
        },
        {
            "label": "Swap",
            "value": f"{swap_pct:.0f}%" if reading.swap_total else "нет",
            "hint": f"{format_bytes(reading.swap_used)} из {format_bytes(reading.swap_total)}" if reading.swap_total else "Файл подкачки не создан",
            "state": _state(swap_pct, 50, server_stats.SWAP_WARN_PERCENT) if reading.swap_total else "info",
        },
        {
            "label": "Диск",
            "value": f"{disk_pct:.0f}%",
            "hint": f"свободно {format_bytes(reading.disk_total - reading.disk_used)} из {format_bytes(reading.disk_total)}",
            "state": _state(disk_pct, 85, 95),
        },
        {
            "label": "Работает без перезагрузки",
            "value": server_stats.format_uptime(reading.uptime_seconds) if reading.uptime_seconds else "—",
            "hint": "время с последней перезагрузки VPS",
            "state": "info",
        },
    ]


def _charts(range_code: str) -> list[dict]:
    rows = server_stats.history(range_code)
    return [
        {
            "key": "cpu",
            "title": "Процессор",
            "chart": server_stats.build_percent_chart(rows, "cpu", note=lambda row: f"load {row['load']:.2f}" if row.get("load") is not None else ""),
        },
        {
            "key": "mem",
            "title": "Память",
            "chart": server_stats.build_percent_chart(
                rows, "mem", note=lambda row: f"пик {row['mem_peak']:.0f}% · swap {row['swap']:.0f}%" if row.get("swap") is not None else ""
            ),
        },
        {"key": "disk", "title": "Диск", "chart": server_stats.build_percent_chart(rows, "disk")},
    ]


def _containers(reading) -> list[dict]:
    peaks = server_stats.container_peaks()
    rows = []
    for item in reading.containers:
        rows.append(
            {
                **item,
                "mem_label": format_bytes(item["mem"]) if item.get("mem") else "—",
                "peak_label": format_bytes(peaks[item["name"]]) if peaks.get(item["name"]) else "—",
                "mem_share": round(item["mem"] * 100 / reading.mem_total, 1) if item.get("mem") and reading.mem_total else None,
            }
        )
    return rows


def _handle_post(request, config) -> None:
    action = request.POST.get("action", "")
    if action == "topup":
        topup, forecast = hosting.record_topup(request.POST.get("amount"), user=request.user)
        log_admin_action(
            actor=request.user, action=AuditAction.SETTINGS_UPDATE,
            summary=f"Хостинг: баланс пополнен на {topup.amount_rub} ₽ → {topup.balance_after_rub} ₽",
            payload={"topup": str(topup.amount_rub), "balance": str(topup.balance_after_rub)}, request=request,
        )
        if forecast.renew_by:
            messages.success(
                request,
                f"Пополнение на {hosting.money_label(topup.amount_rub)} записано. Баланс {hosting.money_label(forecast.balance)}. "
                f"Следующее пополнение — до {timezone.localtime(forecast.renew_by):%d.%m.%Y}, "
                f"на {hosting.money_label(forecast.next_topup_amount)} ({forecast.topup_months} мес.).",
            )
        else:
            messages.success(request, f"Пополнение записано. Баланс {hosting.money_label(forecast.balance)}.")
    elif action == "set_balance":
        config = hosting.set_balance(request.POST.get("balance"))
        log_admin_action(actor=request.user, action=AuditAction.SETTINGS_UPDATE, summary=f"Хостинг: баланс указан {config.balance_rub} ₽", request=request)
        messages.success(request, "Баланс сохранён.")
    elif action == "settings":
        changes = hosting.save_settings(request.POST)
        if changes:
            log_admin_action(actor=request.user, action=AuditAction.SETTINGS_UPDATE, summary=("Хостинг: " + "; ".join(changes))[:255], payload={"changes": changes}, request=request)
            messages.success(request, "Настройки сохранены.")
        else:
            messages.info(request, "Изменений нет.")
    elif action == "item_save":
        item_id = request.POST.get("item_id")
        item = get_object_or_404(HostingItem, pk=item_id) if item_id else None
        was = f"{item.plan} {item.price_rub} {item.get_period_display()}" if item else ""
        item = hosting.save_item(request.POST, item=item)
        now = f"{item.plan} {item.price_rub} {item.get_period_display()}"
        log_admin_action(actor=request.user, action=AuditAction.SETTINGS_UPDATE, summary=f"Хостинг: {item.name}: {was + ' → ' if was else ''}{now}"[:255], request=request)
        messages.success(request, f"«{item.name}» сохранено. Расход: {hosting.money_label(hosting.build_forecast().monthly_cost)} в месяц.")
    elif action == "item_delete":
        item = get_object_or_404(HostingItem, pk=request.POST.get("item_id"))
        item.is_active = False
        item.save(update_fields=["is_active"])
        log_admin_action(actor=request.user, action=AuditAction.SETTINGS_UPDATE, summary=f"Хостинг: убрана услуга «{item.name}»"[:255], request=request)
        messages.success(request, f"«{item.name}» убрано из расходов.")
    elif action == "api_sync":
        if hosting.sync_from_api(config):
            messages.success(request, "Баланс получен из API провайдера.")
        else:
            config.refresh_from_db()
            messages.error(request, config.api_error or "API не настроено: добавьте REGRU_CLOUDVPS_TOKEN в .env.prod.")
    elif action == "snapshot":
        server_stats.take_snapshot()
        messages.success(request, "Снимок нагрузки сохранён.")
    else:
        messages.error(request, "Неизвестное действие.")


@login_required
def server(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    config = HostingSettings.load()
    if request.method == "POST":
        try:
            _handle_post(request, config)
        except hosting.HostingError as exc:
            messages.error(request, str(exc))
        return redirect(reverse("control_center:server") + "#" + (request.POST.get("anchor") or "hosting"))

    range_code = server_stats.normalize_range(request.GET.get("range", ""))
    reading = server_stats.read_server()
    last_snapshot = ServerMetricSnapshot.objects.order_by("-created_at").first()
    if not reading.containers and last_snapshot and last_snapshot.containers:
        reading.containers = last_snapshot.containers
    edit_id = request.GET.get("edit")
    forecast = hosting.build_forecast(config)
    items = list(HostingItem.objects.filter(is_active=True))
    context = {
        "role_access": role_access,
        "config": config,
        "now_cards": _now_cards(reading, config),
        "charts": _charts(range_code),
        "range_code": range_code,
        "range_links": [
            {"code": code, "label": label, "url": f"?range={code}#load", "is_active": code == range_code}
            for code, (label, _period, _bucket) in server_stats.RANGES.items()
        ],
        "containers": _containers(reading),
        "containers_live": bool(reading.containers_at),
        "last_snapshot": last_snapshot,
        "snapshots_count": ServerMetricSnapshot.objects.count(),
        "forecast": forecast,
        "renewals": hosting.renewals(config),
        "balance_items": [item for item in items if item.billing == HostingItem.Billing.BALANCE],
        "edit_item": next((item for item in items if str(item.pk) == edit_id), None),
        "kind_options": HostingItem.Kind.choices,
        "billing_options": HostingItem.Billing.choices,
        "period_options": HostingItem.Period.choices,
        "topups": HostingTopUp.objects.select_related("created_by")[:10],
        "api_configured": bool(hosting.api_token()),
    }
    return _render(request, partial="control_center/partials/server_content.html", context=context, title="Сервер · Control Center", tab="server")
