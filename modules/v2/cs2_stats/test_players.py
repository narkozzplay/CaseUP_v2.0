from __future__ import annotations

import urllib.error
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile

from .models import FaceitMatch, TrackedPlayer
from .players import (
    MATCH_PAGE_SIZE,
    import_faceit_matches,
    lookup_player,
    parse_player_query,
)
from .tasks import refresh_tracked_players_task


User = get_user_model()
STEAM_ID = "76561198000000001"
FACEIT_ID = "fc-1"


def _http_error(url: str, code: int):
    return urllib.error.HTTPError(url, code, "err", {}, None)


def _match_item(n: int) -> dict:
    return {"stats": {
        "Match Id": f"m{n}", "Map": "de_mirage", "Result": "1" if n % 2 else "0", "Score": "13 / 9",
        "Kills": "20", "Deaths": "15", "Assists": "4", "Headshots %": "50", "K/D Ratio": "1.33",
        "K/R Ratio": "0.9", "ADR": "88.5", "MVPs": "3", "Match Finished At": 1700000000000 - n * 1000,
    }}


class FakeApis:
    """Answers the Steam / FACEIT URLs the lookup calls."""

    def __init__(self, *, matches: int = 3, elo_history: list | None = None, private: bool = False, faceit: bool = True):
        self.matches = matches
        self.elo_history = elo_history
        self.private = private
        self.faceit = faceit
        self.calls: list[str] = []

    def __call__(self, url, headers=None, timeout=12.0):
        self.calls.append(url)
        if "ResolveVanityURL" in url:
            return {"response": {"success": 1, "steamid": STEAM_ID}} if "vanityurl=good" in url else {"response": {"success": 42}}
        if "GetPlayerSummaries" in url:
            return {"response": {"players": [{
                "steamid": STEAM_ID, "personaname": "s1mple-fan", "avatarfull": "https://a/x.jpg",
                "profileurl": "https://steamcommunity.com/id/good/", "communityvisibilitystate": 1 if self.private else 3,
                "loccountrycode": "RU", "timecreated": 1300000000,
            }]}}
        if "GetPlayerBans" in url:
            return {"players": [{"NumberOfVACBans": 1, "NumberOfGameBans": 0, "DaysSinceLastBan": 400}]}
        if "GetUserStatsForGame" in url:
            return {"playerstats": {"stats": [
                {"name": "total_kills", "value": 1000}, {"name": "total_deaths", "value": 800},
                {"name": "total_kills_headshot", "value": 500}, {"name": "total_matches_played", "value": 90},
                {"name": "total_wins_map_de_dust2", "value": 50}, {"name": "total_rounds_map_de_dust2", "value": 90},
            ]}}
        if "GetOwnedGames" in url:
            return {"response": {"games": [{"appid": 730, "playtime_forever": 6000}]}}
        if "open.faceit.com/data/v4/players?" in url:
            if not self.faceit:
                raise _http_error(url, 404)
            return {"player_id": FACEIT_ID, "nickname": "fanboy", "country": "ru",
                    "games": {"cs2": {"skill_level": 8, "faceit_elo": 1700, "game_player_id": STEAM_ID}}}
        if url.endswith(f"/players/{FACEIT_ID}/stats/cs2"):
            return {"lifetime": {"Matches": "321", "Wins": "170", "Win Rate %": "53", "Average K/D Ratio": "1.12",
                                 "Average Headshots %": "48", "Longest Win Streak": "9", "Recent Results": ["1", "0", "1"]},
                    "segments": [{"type": "Map", "label": "de_mirage", "stats": {"Matches": "100", "Win Rate %": "55", "Average K/D Ratio": "1.2"}}]}
        if f"/players/{FACEIT_ID}/games/cs2/stats" in url:
            offset = int(url.split("offset=")[1].split("&")[0])
            items = [_match_item(n) for n in range(offset, min(offset + MATCH_PAGE_SIZE, self.matches))]
            return {"items": items}
        if "faceit.com/api/stats/v1" in url:
            if self.elo_history is None:
                raise _http_error(url, 403)
            page = int(url.split("page=")[1].split("&")[0])
            return self.elo_history if page == 0 else []
        raise AssertionError(f"unexpected url {url}")


def _patch_apis(fake: FakeApis):
    return patch.multiple(
        "modules.v2.cs2_stats.players",
        _get_json=fake,
        _steam_api_key=lambda: "steam-key",
        _faceit_api_key=lambda: "faceit-key",
        PAGE_DELAY_SECONDS=0,
    )


def _patch_services(fake: FakeApis):
    # _fetch_steam_cs2_stats / _fetch_steam_owned_cs2 live in services.py.
    return patch.multiple(
        "modules.v2.cs2_stats.services",
        _get_json=fake,
        _steam_api_key=lambda: "steam-key",
    )


class ParseQueryTests(TestCase):
    def test_formats(self):
        self.assertEqual(parse_player_query(STEAM_ID), ("steam_id", STEAM_ID))
        self.assertEqual(parse_player_query(f"https://steamcommunity.com/profiles/{STEAM_ID}/"), ("steam_id", STEAM_ID))
        self.assertEqual(parse_player_query("https://steamcommunity.com/id/good/"), ("vanity", "good"))
        self.assertEqual(parse_player_query("STEAM_1:1:19867136"), ("steam_id", STEAM_ID))
        self.assertEqual(parse_player_query("[U:1:39734273]"), ("steam_id", "76561198000000001"))
        self.assertEqual(parse_player_query("https://www.faceit.com/ru/players/fanboy"), ("faceit", "fanboy"))
        self.assertEqual(parse_player_query("fanboy"), ("text", "fanboy"))


class LookupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="viewer", password="pass")

    def _lookup(self, query: str, fake: FakeApis):
        # The full backfill is queued on commit and runs eagerly in tests.
        with _patch_apis(fake), _patch_services(fake), self.captureOnCommitCallbacks(execute=True):
            return lookup_player(query, user=self.user)

    def test_lookup_by_vanity_saves_player_and_matches(self):
        fake = FakeApis(matches=3, elo_history=[{"matchId": "m0", "elo": "1650"}, {"matchId": "m2", "elo": "2010", "date": 1690000000000}])
        result = self._lookup("https://steamcommunity.com/id/good", fake)

        self.assertTrue(result.ok, result.message)
        player = TrackedPlayer.objects.get(steam_id=STEAM_ID)
        self.assertEqual(player.persona_name, "s1mple-fan")
        self.assertEqual(player.vac_bans, 1)
        self.assertEqual(player.steam_stats["kills"], 1000)
        self.assertEqual(player.playtime_hours, 100)
        self.assertEqual(player.faceit_elo, 1700)
        self.assertEqual(player.faceit_lifetime["matches"], 321)
        self.assertEqual(player.faceit_maps[0]["map_code"], "de_mirage")
        self.assertEqual(FaceitMatch.objects.filter(player=player).count(), 3)
        self.assertTrue(player.faceit_history_complete)
        # Backfill ran eagerly: the ELO history gives the all-time best.
        self.assertEqual(player.faceit_max_elo, 2010)
        self.assertEqual(player.faceit_max_elo_source, TrackedPlayer.MaxEloSource.HISTORY)
        self.assertEqual(FaceitMatch.objects.get(player=player, match_id="m2").elo, 2010)
        self.assertEqual(player.added_by, self.user)

    def test_without_elo_history_falls_back_to_tracked_peak(self):
        self._lookup(STEAM_ID, FakeApis(matches=2, elo_history=None))
        player = TrackedPlayer.objects.get(steam_id=STEAM_ID)
        self.assertEqual(player.faceit_max_elo, 1700)
        self.assertEqual(player.faceit_max_elo_source, TrackedPlayer.MaxEloSource.TRACKED)

    def test_private_profile_and_no_faceit(self):
        result = self._lookup(STEAM_ID, FakeApis(private=True, faceit=False))
        self.assertTrue(result.ok)
        player = result.player
        self.assertEqual(player.steam_error, "private")
        self.assertEqual(player.steam_stats, {})
        self.assertEqual(player.faceit_error, "no_account")

    def test_faceit_nickname_lookup(self):
        result = self._lookup("https://www.faceit.com/en/players/fanboy", FakeApis(matches=0))
        self.assertTrue(result.ok)
        self.assertEqual(result.player.steam_id, STEAM_ID)

    def test_unknown_player(self):
        fake = FakeApis(faceit=False)
        result = self._lookup("https://steamcommunity.com/id/nobody", fake)
        self.assertFalse(result.ok)
        self.assertFalse(TrackedPlayer.objects.exists())

    def test_second_view_uses_stored_data(self):
        fake = FakeApis(matches=1, elo_history=[])
        self._lookup(STEAM_ID, fake)
        calls = len(fake.calls)
        result = self._lookup(STEAM_ID, fake)
        self.assertEqual(len(fake.calls), calls)
        self.assertEqual(result.player.lookup_count, 2)

    def test_max_elo_reaches_caseup_profile(self):
        owner = User.objects.create_user(username="owner", password="pass")
        UserProfile.objects.update_or_create(user=owner, defaults={"steam_id": STEAM_ID})
        self._lookup(STEAM_ID, FakeApis(matches=1, elo_history=[{"matchId": "m0", "elo": "2222"}]))
        self.assertEqual(UserProfile.objects.get(user=owner).faceit_max_elo, 2222)


class MatchImportTests(TestCase):
    def setUp(self):
        self.player = TrackedPlayer.objects.create(steam_id=STEAM_ID, faceit_player_id=FACEIT_ID)

    def test_full_history_then_only_new(self):
        fake = FakeApis(matches=250)
        with _patch_apis(fake):
            self.assertEqual(import_faceit_matches(self.player), 250)
            self.assertTrue(self.player.faceit_history_complete)
            fake.calls.clear()
            self.assertEqual(import_faceit_matches(self.player), 0)
        # Incremental run stops on the first page full of known matches.
        self.assertEqual(len(fake.calls), 1)

    def test_offset_cap_marks_history_done(self):
        fake = FakeApis(matches=10_000)
        original = fake.__call__

        def capped(url, headers=None, timeout=12.0):
            if "offset=200" in url:
                raise _http_error(url, 400)
            return original(url, headers, timeout)

        with _patch_apis(capped):
            self.assertEqual(import_faceit_matches(self.player), 200)
        self.assertTrue(self.player.faceit_history_complete)


class RefreshTaskTests(TestCase):
    def test_enrolls_caseup_users_and_refreshes(self):
        owner = User.objects.create_user(username="owner", password="pass")
        UserProfile.objects.update_or_create(user=owner, defaults={"steam_id": STEAM_ID})
        fake = FakeApis(matches=1, elo_history=None)
        with _patch_apis(fake), _patch_services(fake):
            result = refresh_tracked_players_task()
        self.assertEqual(result["enrolled"], 1)
        self.assertEqual(result["refreshed"], 1)
        player = TrackedPlayer.objects.get(steam_id=STEAM_ID)
        self.assertIsNotNone(player.last_synced_at)
        self.assertEqual(player.faceit_matches.count(), 1)


class PlayerViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="viewer", password="pass")
        self.client.force_login(self.user)

    def test_search_redirects_to_player_page(self):
        fake = FakeApis(matches=2, elo_history=[])
        with _patch_apis(fake), _patch_services(fake):
            response = self.client.get(reverse("cs2_stats:players"), {"q": STEAM_ID})
            self.assertRedirects(response, reverse("cs2_stats:player", args=[STEAM_ID]), fetch_redirect_response=False)
            page = self.client.get(reverse("cs2_stats:player", args=[STEAM_ID]))
        self.assertContains(page, "s1mple-fan")
        self.assertContains(page, "VAC × 1")
        self.assertContains(page, "1700 ELO")
        matches = self.client.get(reverse("cs2_stats:player_matches", args=[STEAM_ID]))
        self.assertContains(matches, "de_mirage".replace("de_", "").title())
        listing = self.client.get(reverse("cs2_stats:players"))
        self.assertContains(listing, "s1mple-fan")

    def test_search_error_is_shown(self):
        fake = FakeApis(faceit=False)
        with _patch_apis(fake), _patch_services(fake):
            response = self.client.get(reverse("cs2_stats:players"), {"q": "https://steamcommunity.com/id/nobody"})
        self.assertContains(response, "Игрок не найден")

    def test_bad_steam_id_is_404(self):
        self.assertEqual(self.client.get("/cs2/stats/players/abc/").status_code, 404)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("cs2_stats:players")).status_code, 302)
