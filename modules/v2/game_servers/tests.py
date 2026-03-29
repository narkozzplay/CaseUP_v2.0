from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import GameServer, GameServerPrivilege
from .services import ServerProbeResult, refresh_game_server_status


class GameServerServiceTests(TestCase):
    def setUp(self):
        self.server = GameServer.objects.create(
            slug="public",
            name="CS2 Public",
            host="127.0.0.1",
            port=27015,
            server_path=r"W:\CaseUP_public",
        )

    @patch("modules.v2.game_servers.services.probe_game_server")
    def test_refresh_game_server_status_marks_server_online(self, probe_mock):
        probe_mock.return_value = ServerProbeResult(
            status=GameServer.Status.ONLINE,
            checked_at=timezone.now(),
            latency_ms=12,
            error="",
        )

        server, result = refresh_game_server_status(self.server, force=True)

        self.assertEqual(server.status, GameServer.Status.ONLINE)
        self.assertIsNotNone(server.last_check_at)
        self.assertIsNotNone(server.last_seen_online_at)
        self.assertEqual(server.last_latency_ms, 12)
        self.assertEqual(result.status, GameServer.Status.ONLINE)
        self.assertEqual(server.status_checks.count(), 1)

    @patch("modules.v2.game_servers.services.probe_game_server")
    def test_refresh_game_server_status_marks_server_offline(self, probe_mock):
        probe_mock.return_value = ServerProbeResult(
            status=GameServer.Status.OFFLINE,
            checked_at=timezone.now(),
            latency_ms=None,
            error="connection refused",
        )

        server, result = refresh_game_server_status(self.server, force=True)

        self.assertEqual(server.status, GameServer.Status.OFFLINE)
        self.assertEqual(server.last_error, "connection refused")
        self.assertIsNone(server.last_latency_ms)
        self.assertEqual(result.status, GameServer.Status.OFFLINE)
        self.assertEqual(server.status_checks.count(), 1)


class GameServerViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.server = GameServer.objects.create(
            slug="public",
            name="CS2AGE Public",
            host="127.0.0.1",
            port=27015,
            server_path=r"W:\CaseUP_public",
            shop_label="CaseUP Store",
            shop_description="Будущий магазин привилегий и игровых пакетов.",
        )
        GameServerPrivilege.objects.create(
            server=self.server,
            code="vip-start",
            name="VIP Start",
            privilege_type=GameServerPrivilege.PrivilegeType.VIP,
            price="299.00",
            duration_days=30,
        )

    @patch("modules.v2.game_servers.views.refresh_game_server_status")
    def test_server_list_page_renders(self, refresh_mock):
        refresh_mock.return_value = (self.server, None)

        response = self.client.get(reverse("game_servers:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CS2AGE Public")
        self.assertContains(response, "127.0.0.1:27015")

    @patch("modules.v2.game_servers.views.refresh_game_server_status")
    def test_server_detail_page_renders_privilege_placeholders(self, refresh_mock):
        refresh_mock.return_value = (self.server, None)

        response = self.client.get(reverse("game_servers:detail", kwargs={"slug": self.server.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VIP Start")
        self.assertContains(response, "CaseUP Store")
        self.assertContains(response, self.server.server_path)
