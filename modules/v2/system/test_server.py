from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import hosting, server_stats
from .models import HostingItem, HostingSettings, HostingTopUp, ServerMetricSnapshot

User = get_user_model()

MEMINFO = """MemTotal:        2000000 kB
MemFree:          100000 kB
MemAvailable:     500000 kB
SwapTotal:       1000000 kB
SwapFree:         900000 kB
"""
STAT = """cpu  100 0 100 700 100 0 0 0 0 0
cpu0 50 0 50 350 50 0 0 0 0 0
cpu1 50 0 50 350 50 0 0 0 0 0
"""


def _make_admin(username="srv_admin"):
    from modules.v2.profiles.models import UserProfile, UserRole

    admin = User.objects.create_user(username=username, password="x")
    UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
    return admin


class FakeProcMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.proc = root / "proc"
        self.stats = root / "stats"
        self.proc.mkdir()
        self.stats.mkdir()
        (self.proc / "meminfo").write_text(MEMINFO)
        (self.proc / "loadavg").write_text("0.50 0.40 0.30 1/200 999\n")
        (self.proc / "uptime").write_text("90061.5 1000.0\n")
        (self.proc / "stat").write_text(STAT)
        self.settings_override = override_settings(SERVER_PROC_ROOT=str(self.proc), SERVER_STATS_DIR=str(self.stats))
        self.settings_override.enable()
        self.sleep = patch("modules.v2.system.server_stats.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.settings_override.disable()
        self.tmp.cleanup()
        super().tearDown()

    def write_containers(self):
        ps = [
            {"Names": "caseup_prod-web-1", "State": "running", "Status": "Up 2 hours (healthy)"},
            {"Names": "caseup_prod-celery_worker-1", "State": "running", "Status": "Up 2 hours (healthy)"},
            {"Names": "old-1", "State": "exited", "Status": "Exited (0) 3 days ago"},
        ]
        stats = [
            {"Name": "caseup_prod-web-1", "CPUPerc": "1.50%", "MemUsage": "300MiB / 1.907GiB"},
            {"Name": "caseup_prod-celery_worker-1", "CPUPerc": "12.00%", "MemUsage": "512MiB / 1.907GiB"},
        ]
        (self.stats / "ps.jsonl").write_text("\n".join(json.dumps(row) for row in ps))
        (self.stats / "stats.jsonl").write_text("\n".join(json.dumps(row) for row in stats) + "\nnot json\n")


class ServerStatsTests(FakeProcMixin, TestCase):
    def test_reads_whole_server_from_proc(self):
        reading = server_stats.read_server()
        self.assertEqual(reading.cpu_count, 2)
        self.assertEqual(reading.mem_total, 2000000 * 1024)
        self.assertEqual(reading.mem_available, 500000 * 1024)
        self.assertEqual(reading.swap_used, 100000 * 1024)
        self.assertEqual(reading.load5, 0.4)
        self.assertEqual(reading.uptime_seconds, 90061)
        self.assertEqual(server_stats.format_uptime(reading.uptime_seconds), "1 д 1 ч")

    def test_cpu_percent_between_snapshots(self):
        server_stats.cpu_percent(remember=True)
        # 100 busy + 100 idle jiffies later → 50%.
        (self.proc / "stat").write_text("cpu  150 0 150 750 150 0 0 0 0 0\ncpu0 1 0 0 0\n")
        self.assertEqual(server_stats.cpu_percent(), 50.0)

    def test_containers_from_sidecar_files(self):
        self.write_containers()
        containers, _at = server_stats.read_containers()
        self.assertEqual([c["name"] for c in containers], ["caseup_prod-celery_worker-1", "caseup_prod-web-1", "old-1"])
        self.assertEqual(containers[0]["mem"], 512 * 1024 * 1024)
        self.assertEqual(containers[0]["cpu"], 12.0)
        self.assertIsNone(containers[2]["cpu"])

    def test_stale_container_files_are_ignored(self):
        import os
        import time

        self.write_containers()
        old = time.time() - server_stats.CONTAINERS_STALE_SECONDS - 60
        os.utime(self.stats / "ps.jsonl", (old, old))
        self.assertEqual(server_stats.read_containers()[0], [])

    def test_parse_size(self):
        self.assertEqual(server_stats.parse_size("1.5GiB"), int(1.5 * 1024**3))
        self.assertEqual(server_stats.parse_size("20kB"), 20000)
        self.assertEqual(server_stats.parse_size("0B"), 0)
        self.assertEqual(server_stats.parse_size("garbage"), 0)

    def test_snapshot_stored_and_old_ones_pruned(self):
        ServerMetricSnapshot.objects.create(created_at=timezone.now() - timedelta(days=40))
        self.write_containers()
        snapshot = server_stats.take_snapshot()
        self.assertEqual(ServerMetricSnapshot.objects.count(), 1)
        self.assertEqual(round(snapshot.mem_percent), 75)
        self.assertEqual(len(snapshot.containers), 3)
        self.assertEqual(server_stats.container_peaks()["caseup_prod-web-1"], 300 * 1024 * 1024)

    def test_history_buckets_and_chart(self):
        now = timezone.now()
        for minutes in range(0, 600, 5):
            ServerMetricSnapshot.objects.create(
                created_at=now - timedelta(minutes=minutes), cpu_percent=10 + minutes % 20, mem_total=100, mem_available=40,
                disk_total=100, disk_used=50,
            )
        day = server_stats.history("day")
        week = server_stats.history("week")
        self.assertEqual(len(day), 120)
        self.assertLessEqual(len(week), 11)
        chart = server_stats.build_percent_chart(day, "mem")
        self.assertTrue(chart["has_data"])
        self.assertEqual(chart["max"], 60)
        self.assertEqual(chart["points_json"][0]["price"], "60")
        self.assertEqual(server_stats.build_percent_chart([], "mem"), {"has_data": False})

    @patch("modules.v2.system.alerts._send_telegram", return_value=True)
    def test_memory_alert_only_when_it_stays_high(self, telegram):
        now = timezone.now()
        for index, available in enumerate((5, 40, 5)):
            ServerMetricSnapshot.objects.create(created_at=now - timedelta(minutes=index * 5), mem_total=100, mem_available=available)
        self.assertEqual(server_stats.check_overload(), {})
        ServerMetricSnapshot.objects.filter(mem_available=40).update(mem_available=5)
        self.assertEqual(server_stats.check_overload(), {"memory": "alert"})
        self.assertIn("Память сервера", telegram.call_args.args[0])
        ServerMetricSnapshot.objects.update(mem_available=60)
        self.assertEqual(server_stats.check_overload(), {"memory": "recovered"})


class HostingForecastTests(TestCase):
    def setUp(self):
        cache.clear()
        HostingItem.objects.all().delete()
        HostingSettings.objects.all().delete()
        self.config = HostingSettings.load()
        HostingItem.objects.create(name="CaseUP_prod", kind="server", price_rub=Decimal("1.76"), period="hour")
        HostingItem.objects.create(name="IP", kind="ip", price_rub=Decimal("187.38"), period="month")

    def test_seeded_production_server(self):
        # The data migration describes the server as of 30.10.2026; this test class wipes it, so check the
        # cost math on the same numbers: 1,76 ₽/ч + 187,38 ₽/мес ≈ 2,02 ₽/ч.
        hourly, source = hosting.hourly_cost(self.config)
        self.assertEqual(source, "items")
        self.assertAlmostEqual(float(hourly), 2.0203, places=3)

    def test_forecast_keeps_seven_days_reserve(self):
        now = timezone.now()
        self.config.balance_rub = Decimal("728.85")
        self.config.balance_at = now
        self.config.save()
        forecast = hosting.build_forecast(self.config, now=now)
        self.assertEqual(forecast.balance, Decimal("728.85"))
        self.assertEqual(forecast.reserve_rub, Decimal("339.40"))  # 7 × 24 × 2,02025
        self.assertEqual(forecast.available, Decimal("389.45"))
        self.assertAlmostEqual(forecast.days_left, 15.03, places=1)
        self.assertAlmostEqual(forecast.days_to_renew, 8.03, places=1)
        self.assertEqual(forecast.state, "ok")
        # A month of work + the reserve, minus what is there.
        self.assertEqual(forecast.topup_amount, Decimal("1065.13"))
        self.assertEqual(forecast.next_topup_amount, Decimal("1454.58"))

    def test_balance_goes_down_by_the_hour(self):
        now = timezone.now()
        self.config.balance_rub = Decimal("100")
        self.config.balance_at = now - timedelta(hours=10)
        self.config.save()
        forecast = hosting.build_forecast(self.config, now=now)
        self.assertEqual(forecast.balance, Decimal("79.80"))
        self.assertEqual(forecast.state, "fail")

    def test_topup_adds_to_current_balance(self):
        self.config.balance_rub = Decimal("100")
        self.config.balance_at = timezone.now()
        self.config.save()
        topup, forecast = hosting.record_topup("1000,50")
        self.assertEqual(topup.amount_rub, Decimal("1000.50"))
        self.assertEqual(forecast.balance, Decimal("1100.50"))
        self.assertIsNotNone(forecast.renew_by)
        with self.assertRaises(hosting.HostingError):
            hosting.record_topup("-5")

    def test_tariff_change_changes_forecast(self):
        server = HostingItem.objects.get(kind="server")
        hosting.save_item({"name": server.name, "kind": "server", "billing": "balance", "plan": "C4-M4", "price_rub": "3.5", "period": "hour"}, item=server)
        hourly, _ = hosting.hourly_cost(self.config)
        self.assertAlmostEqual(float(hourly), 3.76, places=2)

    def test_renewal_needs_a_date(self):
        with self.assertRaises(hosting.HostingError):
            hosting.save_item({"name": "Хостинг", "kind": "hosting", "billing": "renewal", "price_rub": "1500", "period": "year"})

    @patch("modules.v2.system.alerts._send_telegram", return_value=True)
    def test_balance_and_renewal_alerts_once_a_day(self, telegram):
        self.config.balance_rub = Decimal("400")  # ≈ 8 days: inside the 7-day reserve + 3 days warning
        self.config.balance_at = timezone.now()
        self.config.save()
        HostingItem.objects.create(
            name="case-up.ru", kind="domain", billing="renewal", price_rub=Decimal("199"), period="year",
            paid_until=timezone.localdate() + timedelta(days=5),
        )
        report = hosting.run_hosting_check()
        self.assertEqual(report["balance"], "alert")
        self.assertEqual(len(report["renewals"]), 1)
        texts = "\n".join(call.args[0] for call in telegram.call_args_list)
        self.assertIn("пополнить до", texts)
        self.assertIn("Пополните на", texts)
        self.assertIn("case-up.ru", texts)
        again = hosting.run_hosting_check()
        self.assertEqual(again["balance"], "")
        self.assertEqual(again["renewals"], {})

    @override_settings(REGRU_CLOUDVPS_TOKEN="secret")
    @patch("modules.v2.system.hosting.requests.get")
    def test_api_sync(self, get):
        get.return_value = MagicMock(status_code=200, json=lambda: {"balance_data": {
            "balance": 728.85, "bonus_balance": 0, "hourly_cost": 2.02, "monthly_cost": 1454.4, "days_left": 15,
            "detalization": [
                {"name": "CaseUP_prod", "plan": "Stdp C2-M2-D40", "price": "1.76", "price_month": 1267.2, "type": "reglet"},
                {"name": "185.46.11.244", "plan": "ipv4", "price": "0.26", "price_month": 187.38, "type": "ip"},
            ],
        }})
        self.assertTrue(hosting.sync_from_api())
        config = HostingSettings.load()
        self.assertEqual(config.balance_rub, Decimal("728.85"))
        self.assertEqual(config.balance_source, "api")
        self.assertEqual(hosting.hourly_cost(config), (Decimal("2.02"), "api"))
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

        get.return_value = MagicMock(status_code=401)
        self.assertFalse(hosting.sync_from_api())
        config.refresh_from_db()
        self.assertIn("401", config.api_error)
        self.assertEqual(config.balance_rub, Decimal("728.85"))


class ServerPageTests(FakeProcMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = _make_admin()
        self.client.force_login(self.admin)
        self.url = reverse("control_center:server")

    def test_page_for_admins_only(self):
        user = User.objects.create_user(username="plain", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_page_shows_balance_and_load(self):
        self.write_containers()
        server_stats.take_snapshot()
        response = self.client.get(self.url + "?range=week")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Пополнить до", content)
        self.assertIn("CaseUP_prod", content)  # seeded by the data migration
        self.assertIn("Stdp C2-M2-D40", content)
        self.assertIn("Хост-А", content)  # renewals seeded by 0027
        self.assertIn("14.11.2026", content)
        self.assertIn("caseup_prod-celery_worker-1", content)
        self.assertIn("data-price-chart", content)

    def test_topup_button_shows_next_date(self):
        response = self.client.post(self.url, {"action": "topup", "amount": "500"}, follow=True)
        self.assertEqual(HostingTopUp.objects.count(), 1)
        self.assertContains(response, "Следующее пополнение")

    def test_edit_tariff_and_add_ip(self):
        server = HostingItem.objects.get(kind="server")
        self.client.post(self.url, {"action": "item_save", "item_id": server.pk, "name": "CaseUP_prod", "kind": "server", "billing": "balance", "plan": "4 vCPU, 4 ГБ", "price_rub": "3.2", "period": "hour"})
        self.client.post(self.url, {"action": "item_save", "name": "IP 2", "kind": "ip", "billing": "balance", "price_rub": "187.38", "period": "month"})
        server.refresh_from_db()
        self.assertEqual(server.price_rub, Decimal("3.2"))
        self.assertEqual(HostingItem.objects.filter(kind="ip", is_active=True).count(), 2)

    def test_settings_save(self):
        response = self.client.post(self.url, {
            "action": "settings", "provider_name": "Рег.облако", "panel_url": "https://cloud.reg.ru/panel/",
            "reserve_days": "10", "balance_warn_days": "3", "renewal_warn_days": "14", "topup_months": "2",
            "ram_warn_percent": "85", "monthly_cost_rub": "", "alerts_enabled": "1",
        })
        self.assertEqual(response.status_code, 302)
        config = HostingSettings.load()
        self.assertEqual(config.reserve_days, 10)
        self.assertEqual(config.topup_months, 2)
