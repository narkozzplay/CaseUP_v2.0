"""PandaScore REST API: finished CS2 matches of a Major (score, maps, stage) for the channel posts.

The free plan gives schedules and results: who won the series and each map. Map names and round scores live in
game details, which some plans do not give; the post then goes out without them. Player stats (MVP) are paid,
so the MVP is entered by hand on the post page in Control Center.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import requests
from django.utils.dateparse import parse_datetime

from modules.v2.system.services import get_integration_secret

API_BASE = "https://api.pandascore.co"
# PandaScore kept the old "csgo" slug for Counter-Strike 2.
GAME = "csgo"
TIMEOUT = 10
PAGE_SIZE = 100


class PandaScoreError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class MapResult:
    name: str = ""
    winner: str = ""
    # Rounds won by team A and team B, when the plan gives them.
    score_a: int | None = None
    score_b: int | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "winner": self.winner, "score_a": self.score_a, "score_b": self.score_b}


@dataclass
class TeamRef:
    id: int
    name: str
    acronym: str = ""


@dataclass
class FinishedMatch:
    id: int
    name: str
    tournament_id: int | None
    tournament_name: str
    team_a: TeamRef
    team_b: TeamRef
    score_a: int
    score_b: int
    winner_id: int | None
    finished_at: datetime | None
    forfeit: bool = False
    game_ids: list[int] = field(default_factory=list)
    maps: list[MapResult] = field(default_factory=list)

    @property
    def winner(self) -> TeamRef | None:
        return {self.team_a.id: self.team_a, self.team_b.id: self.team_b}.get(self.winner_id)

    @property
    def loser(self) -> TeamRef | None:
        winner = self.winner
        if winner is None:
            return None
        return self.team_b if winner is self.team_a else self.team_a


def api_key() -> str:
    return get_integration_secret("pandascore_api_key")


def _get(path: str, *, params: dict | None = None, key: str | None = None):
    key = key if key is not None else api_key()
    if not key:
        raise PandaScoreError("Ключ PandaScore не задан (System Settings → Интеграции).")
    try:
        response = requests.get(f"{API_BASE}{path}", params=params or {}, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise PandaScoreError(f"PandaScore недоступен: {exc.__class__.__name__}.") from exc
    if response.status_code in (401, 403):
        raise PandaScoreError(f"PandaScore отклонил запрос ({response.status_code}): ключ неверный или тариф не даёт эти данные.", status_code=response.status_code)
    if response.status_code == 429:
        raise PandaScoreError("PandaScore: превышен лимит запросов.", status_code=429)
    if not response.ok:
        raise PandaScoreError(f"PandaScore вернул HTTP {response.status_code}.", status_code=response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise PandaScoreError("PandaScore вернул не JSON.", status_code=response.status_code) from exc


def check_key(key: str) -> str:
    """One cheap request; the text for the integrations page."""
    _get(f"/{GAME}/series/running", params={"page[size]": 1}, key=key)
    return "PandaScore отвечает, ключ принят."


def find_series(query: str = "") -> list[dict]:
    """Running and upcoming CS2 series (a Major is one series), optionally filtered by name, for picking the ID."""
    rows: list[dict] = []
    for kind in ("running", "upcoming"):
        for serie in _get(f"/{GAME}/series/{kind}", params={"page[size]": 50}) or []:
            league = (serie.get("league") or {}).get("name") or ""
            name = serie.get("full_name") or serie.get("name") or ""
            title = f"{league} {name}".strip()
            if query and query.lower() not in title.lower():
                continue
            rows.append({"id": serie.get("id"), "title": title, "begin_at": parse_datetime(serie.get("begin_at") or "") if serie.get("begin_at") else None, "running": kind == "running"})
    return rows


def _team(opponent: dict) -> TeamRef:
    team = opponent.get("opponent") or opponent
    return TeamRef(id=int(team.get("id") or 0), name=str(team.get("name") or "TBD"), acronym=str(team.get("acronym") or ""))


def _map_name(raw) -> str:
    name = (raw or {}).get("name") if isinstance(raw, dict) else raw
    name = str(name or "").strip()
    if name.lower().startswith("de_"):
        name = name[3:]
    return name[:1].upper() + name[1:] if name else ""


def _game_scores(game: dict, team_a_id: int, team_b_id: int) -> tuple[int | None, int | None]:
    """Rounds per team from whichever shape the plan returns: teams[] or rounds_score[]."""
    scores: dict[int, int] = {}
    for row in (game.get("teams") or []) + (game.get("rounds_score") or []):
        if not isinstance(row, dict):
            continue
        team_id = row.get("team_id") or (row.get("team") or {}).get("id")
        value = next((row.get(key) for key in ("score", "rounds_score", "round_score", "rounds") if isinstance(row.get(key), int)), None)
        if team_id and value is not None:
            scores[int(team_id)] = value
    return scores.get(team_a_id), scores.get(team_b_id)


def _map_result(game: dict, team_a: TeamRef, team_b: TeamRef) -> MapResult:
    winner_id = (game.get("winner") or {}).get("id")
    winner = {team_a.id: team_a.name, team_b.id: team_b.name}.get(winner_id, "")
    score_a, score_b = _game_scores(game, team_a.id, team_b.id)
    return MapResult(name=_map_name(game.get("map")), winner=winner, score_a=score_a, score_b=score_b)


def parse_match(raw: dict) -> FinishedMatch | None:
    opponents = raw.get("opponents") or []
    if len(opponents) < 2:
        return None
    team_a, team_b = _team(opponents[0]), _team(opponents[1])
    results = {int(row.get("team_id") or 0): int(row.get("score") or 0) for row in raw.get("results") or []}
    tournament = raw.get("tournament") or {}
    games = sorted((game for game in raw.get("games") or [] if isinstance(game, dict)), key=lambda game: game.get("position") or 0)
    played = [game for game in games if game.get("winner") and (game.get("winner") or {}).get("id")]
    finished_at = parse_datetime(raw.get("end_at") or "") if raw.get("end_at") else None
    return FinishedMatch(
        id=int(raw["id"]),
        name=str(raw.get("name") or ""),
        tournament_id=raw.get("tournament_id") or tournament.get("id"),
        tournament_name=str(tournament.get("name") or ""),
        team_a=team_a,
        team_b=team_b,
        score_a=results.get(team_a.id, 0),
        score_b=results.get(team_b.id, 0),
        winner_id=raw.get("winner_id"),
        finished_at=finished_at,
        forfeit=bool(raw.get("forfeit")),
        game_ids=[int(game["id"]) for game in played if game.get("id")],
        maps=[_map_result(game, team_a, team_b) for game in played],
    )


def finished_matches(serie_id: int) -> list[FinishedMatch]:
    """The latest finished matches of the series, oldest first."""
    raw = _get(f"/{GAME}/matches", params={"filter[serie_id]": serie_id, "filter[status]": "finished", "sort": "-end_at", "page[size]": PAGE_SIZE})
    matches = [match for match in (parse_match(row) for row in raw or []) if match is not None and match.winner_id]
    return sorted(matches, key=lambda match: (match.finished_at is None, match.finished_at or datetime.min, match.id))


def fill_maps(match: FinishedMatch) -> None:
    """Map names and round scores from the game details, where the match list left them out. Best effort."""
    if all(item.name for item in match.maps) or not match.game_ids:
        return
    detailed = []
    for game_id in match.game_ids:
        try:
            game = _get(f"/{GAME}/games/{game_id}")
        except PandaScoreError:
            return  # The plan does not give game details: the post goes out with winners only.
        detailed.append(_map_result(game or {}, match.team_a, match.team_b))
    for index, item in enumerate(detailed):
        if index < len(match.maps):
            current = match.maps[index]
            current.name = current.name or item.name
            current.winner = current.winner or item.winner
            if current.score_a is None and current.score_b is None:
                current.score_a, current.score_b = item.score_a, item.score_b
