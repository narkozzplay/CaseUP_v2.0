from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile

from .models import CS2MapStat, CS2PlayerStats, FACEITExtendedStats
from .services import (
    SyncResult,
    _parse_map_stats,
    _percent,
    _ratio,
    _safe_float,
    _safe_int,
    get_cs2_stats_context,
)


User = get_user_model()
PARTIAL_HEADERS = {"HTTP_X_DASHBOARD_PARTIAL": "1"}


def _make_user(username: str) -> User:
    return User.objects.create_user(username=username, password="pass")


class HelperTests(TestCase):
    def test_safe_int(self):
        self.assertEqual(_safe_int("42"), 42)
        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int("bad"), 0)

    def test_safe_float(self):
        self.assertAlmostEqual(_safe_float("1.23"), 1.23)
        self.assertEqual(_safe_float(None), 0.0)

    def test_ratio(self):
        self.assertEqual(_ratio(10, 5), 2.0)
        self.assertEqual(_ratio(10, 0), 0.0)

    def test_percent(self):
        self.assertAlmostEqual(_percent(45, 100), 45.0)
        self.assertEqual(_percent(0, 0), 0.0)


class ParseMapStatsTests(TestCase):
    def test_parses_known_maps(self):
        raw = {
            "total_wins_map_de_dust2": 100,
            "total_rounds_map_de_dust2": 200,
            "total_kills_map_de_dust2": 500,
            "total_deaths_map_de_dust2": 400,
            "total_wins_map_de_mirage": 80,
            "total_rounds_map_de_mirage": 160,
        }
        rows = _parse_map_stats(raw)
        codes = [r["map_code"] for r in rows]
        self.assertIn("de_dust2", codes)
        self.assertIn("de_mirage", codes)
        dust2 = next(r for r in rows if r["map_code"] == "de_dust2")
        self.assertEqual(dust2["wins"], 100)
        self.assertAlmostEqual(dust2["win_rate"], 50.0)
        self.assertAlmostEqual(dust2["kd_ratio"], 1.25)

    def test_skips_empty_maps(self):
        raw = {"total_wins_map_de_nuke": 0, "total_rounds_map_de_nuke": 0}
        rows = _parse_map_stats(raw)
        codes = [r["map_code"] for r in rows]
        self.assertNotIn("de_nuke", codes)


class SyncCS2SteamStatsTests(TestCase):
    def setUp(self):
        self.user = _make_user("cs2_user")
        self.user.profile_v2.steam_id = "76561199647175346"
        self.user.profile_v2.save(update_fields=["steam_id"])

    @patch("modules.v2.cs2_stats.services._fetch_steam_cs2_stats")
    @patch("modules.v2.cs2_stats.services._fetch_steam_owned_cs2")
    def test_sync_creates_stats(self, mock_owned, mock_stats):
        from modules.v2.cs2_stats.services import sync_cs2_steam_stats
        mock_stats.return_value = {
            "total_kills": 5000,
            "total_deaths": 4000,
            "total_matches_played": 800,
            "total_wins": 420,
            "total_headshots": 2000,
            "total_mvps": 300,
            "total_time_played": 800000,
            "total_shots_fired": 100000,
            "total_shots_hit": 45000,
            "total_damage_done": 1000000,
            "total_wins_map_de_dust2": 80,
            "total_rounds_map_de_dust2": 160,
        }
        mock_owned.return_value = (90000, None)

        result = sync_cs2_steam_stats(self.user)
        self.assertTrue(result.ok)

        stats = CS2PlayerStats.objects.get(user=self.user)
        self.assertEqual(stats.total_kills, 5000)
        self.assertAlmostEqual(stats.kd_ratio, 1.25)
        self.assertAlmostEqual(stats.hs_percent, 40.0)
        self.assertEqual(stats.playtime_hours, 1500)

        maps = CS2MapStat.objects.filter(user=self.user)
        self.assertTrue(maps.filter(map_code="de_dust2").exists())

    @patch("modules.v2.cs2_stats.services._fetch_steam_cs2_stats")
    def test_sync_empty_stats_fails_gracefully(self, mock_stats):
        from modules.v2.cs2_stats.services import sync_cs2_steam_stats
        mock_stats.return_value = {}
        result = sync_cs2_steam_stats(self.user)
        self.assertFalse(result.ok)

    @patch("modules.v2.cs2_stats.services._fetch_steam_cs2_stats")
    def test_sync_api_error_fails_gracefully(self, mock_stats):
        from modules.v2.cs2_stats.services import sync_cs2_steam_stats
        mock_stats.side_effect = Exception("Connection refused")
        result = sync_cs2_steam_stats(self.user)
        self.assertFalse(result.ok)
        stats = CS2PlayerStats.objects.get(user=self.user)
        self.assertIn("Connection refused", stats.last_sync_error)

    def test_sync_no_steam_id_fails(self):
        from modules.v2.cs2_stats.services import sync_cs2_steam_stats
        self.user.profile_v2.steam_id = None
        self.user.profile_v2.save(update_fields=["steam_id"])
        result = sync_cs2_steam_stats(self.user)
        self.assertFalse(result.ok)


class SyncFACEITExtendedTests(TestCase):
    def setUp(self):
        self.user = _make_user("faceit_ext_user")
        self.user.profile_v2.faceit_player_id = "test-player-id-123"
        self.user.profile_v2.save(update_fields=["faceit_player_id"])

    @patch("modules.v2.cs2_stats.services._faceit_get")
    def test_sync_creates_faceit_stats(self, mock_get):
        from modules.v2.cs2_stats.services import sync_faceit_extended_stats
        mock_get.side_effect = [
            # stats endpoint
            {"lifetime": {
                "Matches": "412",
                "Wins": "220",
                "Average K/D Ratio": "1.31",
                "Average Headshots %": "48.2",
                "Win Rate %": "53.4",
                "Longest Win Streak": "12",
            }},
            # history endpoint
            {"items": [{"started_at": 1717000000}]},
        ]
        result = sync_faceit_extended_stats(self.user)
        self.assertTrue(result.ok)

        ext = FACEITExtendedStats.objects.get(user=self.user)
        self.assertEqual(ext.matches, 412)
        self.assertAlmostEqual(ext.avg_kd, 1.31)
        self.assertIsNotNone(ext.last_match_at)

    def test_sync_no_player_id_fails(self):
        from modules.v2.cs2_stats.services import sync_faceit_extended_stats
        self.user.profile_v2.faceit_player_id = ""
        self.user.profile_v2.save(update_fields=["faceit_player_id"])
        result = sync_faceit_extended_stats(self.user)
        self.assertFalse(result.ok)


class CS2StatsContextTests(TestCase):
    def setUp(self):
        self.user = _make_user("ctx_user")

    def test_context_no_stats(self):
        ctx = get_cs2_stats_context(self.user, viewer=self.user)
        self.assertIsNone(ctx["steam_stats"])
        self.assertIsNone(ctx["faceit_ext"])
        self.assertEqual(ctx["map_rows"], [])
        self.assertTrue(ctx["is_owner"])

    def test_context_with_stats(self):
        CS2PlayerStats.objects.create(
            user=self.user,
            total_kills=1000,
            total_deaths=800,
            kd_ratio=1.25,
        )
        ctx = get_cs2_stats_context(self.user, viewer=self.user)
        self.assertIsNotNone(ctx["steam_stats"])
        self.assertEqual(ctx["steam_stats"].total_kills, 1000)

    def test_context_comparison_requires_both(self):
        ctx = get_cs2_stats_context(self.user, viewer=self.user)
        self.assertIsNone(ctx["comparison"])

        CS2PlayerStats.objects.create(user=self.user, kd_ratio=1.2)
        FACEITExtendedStats.objects.create(user=self.user, avg_kd=1.35)
        ctx = get_cs2_stats_context(self.user, viewer=self.user)
        self.assertIsNotNone(ctx["comparison"])
        self.assertAlmostEqual(ctx["comparison"]["steam_kd"], 1.2)
        self.assertAlmostEqual(ctx["comparison"]["faceit_kd"], 1.35)


class CS2StatsViewTests(TestCase):
    def setUp(self):
        self.user = _make_user("view_user")
        self.client = Client()
        self.client.force_login(self.user)

    def test_own_stats_get(self):
        resp = self.client.get(reverse("cs2_stats:own"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_user_stats_get(self):
        other = _make_user("other_user")
        resp = self.client.get(reverse("cs2_stats:user", args=[other.pk]), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_requires_login(self):
        anon = Client()
        resp = anon.get(reverse("cs2_stats:own"))
        self.assertIn(resp.status_code, [302, 403])

    @patch("modules.v2.cs2_stats.views.sync_cs2_steam_stats")
    def test_sync_steam_post(self, mock_sync):
        mock_sync.return_value = SyncResult(True, "Synced.")
        resp = self.client.post(reverse("cs2_stats:own"), {"action": "sync_steam"})
        self.assertEqual(resp.status_code, 302)
        mock_sync.assert_called_once()

    @patch("modules.v2.cs2_stats.views.sync_faceit_extended_stats")
    def test_sync_faceit_post(self, mock_sync):
        mock_sync.return_value = SyncResult(False, "No FACEIT.")
        resp = self.client.post(reverse("cs2_stats:own"), {"action": "sync_faceit"})
        self.assertEqual(resp.status_code, 302)
