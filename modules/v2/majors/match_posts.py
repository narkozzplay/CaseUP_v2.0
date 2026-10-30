"""Posts about Major matches in the public Telegram channel.

Every minute the finished matches of each Major with posting switched on are read from PandaScore. A match seen for
the first time gets a MatchPost row (the unique PandaScore ID keeps two workers from posting it twice) and goes to
the channel right away: score, maps, and who advanced or went out. The MVP is not in the free data, so it is added
on the post page in Control Center, which edits the message already in the channel.
"""
from __future__ import annotations

import html
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.v2.tg_notifier.services import (
    TelegramNotifierError,
    add_utm,
    call_bot_api,
    get_telegram_notifier_config,
    resolve_public_base_url,
    send_message,
)

from . import pandascore
from .models import Major, MatchPost, Team

# A match first seen later than this after it ended (posting switched on mid-event, worker down for hours) is
# recorded without a post, so the channel does not get a burst of old results. It can still be sent by hand.
FRESH_FOR = timedelta(hours=3)
# Matches are polled from a day before the start to a day after the final.
WINDOW_MARGIN = timedelta(days=1)
HASHTAGS = "#CaseUP #CounterStrike2 #Major"
UTM_CAMPAIGN = "major"
SWISS_WINS, SWISS_LOSSES = 3, 3

PLAYOFF_ROUNDS = [
    # (pattern in the PandaScore match name, round label, where the winner goes)
    (re.compile(r"grand\s*final", re.I), "Финал", ""),
    (re.compile(r"(3rd|third)\s*place", re.I), "Матч за 3-е место", ""),
    (re.compile(r"semi", re.I), "Полуфинал", "в финал"),
    (re.compile(r"quarter", re.I), "Четвертьфинал", "в полуфинал"),
    (re.compile(r"\bfinal", re.I), "Финал", ""),
]


class MatchPostError(RuntimeError):
    pass


# --------------------------------------------------------------------------- building a post


def _team_names() -> dict[str, str]:
    """PandaScore name or acronym (lowercase) -> the short name CaseUP shows for that team."""
    names: dict[str, str] = {}
    for team in Team.objects.all():
        for key in (team.name, team.tag):
            if key:
                names.setdefault(key.strip().lower(), team.short)
    return names


def _display(team: pandascore.TeamRef, names: dict[str, str]) -> str:
    return names.get(team.name.lower()) or names.get(team.acronym.lower()) or team.name


def _is_playoff(match: pandascore.FinishedMatch) -> bool:
    return "playoff" in match.tournament_name.lower() or any(pattern.search(match.name) for pattern, _l, _n in PLAYOFF_ROUNDS[:4])


def _playoff_round(match: pandascore.FinishedMatch) -> tuple[str, str]:
    for pattern, label, goes_to in PLAYOFF_ROUNDS:
        if pattern.search(match.name):
            return label, goes_to
    return "", ""


def _swiss_records(match: pandascore.FinishedMatch, history: list[pandascore.FinishedMatch]) -> dict[int, tuple[int, int]]:
    """Wins and losses of both teams in this stage, up to and including this match."""
    records = {match.team_a.id: [0, 0], match.team_b.id: [0, 0]}
    for other in history:
        if other.tournament_id != match.tournament_id:
            continue
        later = other.finished_at is None or (match.finished_at is not None and other.finished_at > match.finished_at)
        if later and other.id != match.id:
            continue
        for team in (other.team_a, other.team_b):
            if team.id in records:
                records[team.id][0 if team.id == other.winner_id else 1] += 1
    return {team_id: (wins, losses) for team_id, (wins, losses) in records.items()}


def describe_progress(match: pandascore.FinishedMatch, history: list[pandascore.FinishedMatch], *, major: Major, names: dict[str, str]) -> tuple[str, str]:
    """(round label, progress line): who advanced and who went out after this match."""
    winner, loser = match.winner, match.loser
    if winner is None or loser is None:
        return "", ""
    win, lose = _display(winner, names), _display(loser, names)
    if _is_playoff(match):
        label, goes_to = _playoff_round(match)
        if label == "Финал":
            return label, f"🏆 {win} — чемпион {major.name}!"
        if label == "Матч за 3-е место":
            return label, f"{win} занимает 3-е место"
        if goes_to:
            return label, f"{win} проходит {goes_to}, {lose} выбывает"
        return label, f"{win} проходит дальше, {lose} выбывает"
    records = _swiss_records(match, history)
    w_wins, w_losses = records[winner.id]
    l_wins, l_losses = records[loser.id]
    parts = []
    if w_wins >= SWISS_WINS:
        parts.append(f"✅ {win} выходит дальше ({w_wins}-{w_losses})")
    if l_losses >= SWISS_LOSSES:
        parts.append(f"❌ {lose} вылетает ({l_wins}-{l_losses})")
    if not parts:
        parts.append(f"{win} {w_wins}-{w_losses} · {lose} {l_wins}-{l_losses}")
    return "", " · ".join(parts)


def _map_line(item: dict) -> str:
    name = html.escape(item.get("name") or "")
    winner = html.escape(item.get("winner") or "")
    score_a, score_b = item.get("score_a"), item.get("score_b")
    rounds = f" {score_a}:{score_b}" if score_a is not None and score_b is not None else ""
    if name and rounds:
        return f"🗺 {name} —{rounds}" + (f" ({winner})" if winner else "")
    if name:
        return f"🗺 {name}" + (f" — {winner}" if winner else "")
    return f"🗺 Карта — {winner}" if winner else ""


def build_text(post: MatchPost) -> str:
    """The channel post (Telegram HTML)."""
    esc = html.escape
    heading = " · ".join(part for part in (post.major.name, post.stage_name, post.round_name) if part)
    lines = [f"🎮 <b>{esc(heading)}</b>", "", f"<b>{esc(post.team_a)} {post.score_a}:{post.score_b} {esc(post.team_b)}</b>"]
    maps = [line for line in (_map_line(item) for item in post.maps or []) if line]
    if len(maps) > 1 or (maps and not maps[0].startswith("🗺 Карта")):
        lines += [""] + maps
    if post.mvp:
        lines += ["", f"⭐ MVP: <b>{esc(post.mvp)}</b>"]
    if post.progress:
        lines += ["", esc(post.progress)]
    base_url = resolve_public_base_url()
    if base_url:
        config = get_telegram_notifier_config()
        url = add_utm(f"{base_url}{post.major.get_absolute_url()}", source=config.utm_source, medium="channel", campaign=UTM_CAMPAIGN, content="match")
        lines += ["", f'🔮 <a href="{esc(url, quote=True)}">Pick\'em и сетка мейджора на CaseUP</a>']
    lines += ["", HASHTAGS]
    return "\n".join(lines)


# --------------------------------------------------------------------------- sending


def send_post(post: MatchPost) -> MatchPost:
    """Posts to the channel, or edits the message already there. Errors are written to the row and raised."""
    config = get_telegram_notifier_config()
    post.text = build_text(post)
    try:
        if post.message_id and post.chat_id:
            try:
                call_bot_api("editMessageText", {"chat_id": post.chat_id, "message_id": post.message_id, "text": post.text, "parse_mode": "HTML", "disable_web_page_preview": True})
            except TelegramNotifierError as exc:
                if "not modified" not in str(exc).lower():
                    raise
        else:
            chat_id = (config.channel_chat_id or "").strip()
            if not chat_id:
                raise TelegramNotifierError("Канал не указан в настройках Telegram-бота.")
            result = send_message(text=post.text, chat_id=chat_id, parse_mode="HTML")
            post.chat_id = chat_id
            post.message_id = ((result.payload or {}).get("result") or {}).get("message_id")
            post.posted_at = timezone.now()
    except TelegramNotifierError as exc:
        post.error = str(exc)[:2000]
        if not post.message_id:
            post.status = MatchPost.Status.ERROR
        post.save()
        raise MatchPostError(str(exc)) from exc
    post.status, post.error = MatchPost.Status.SENT, ""
    post.save()
    return post


# --------------------------------------------------------------------------- polling


def _record(major: Major, match: pandascore.FinishedMatch, history: list[pandascore.FinishedMatch], names: dict[str, str], *, now) -> MatchPost | None:
    """Creates the row for a newly finished match; None when another worker got there first."""
    pandascore.fill_maps(match)
    round_name, progress = describe_progress(match, history, major=major, names=names)
    team_a, team_b = _display(match.team_a, names), _display(match.team_b, names)
    maps = []
    for item in match.maps:
        row = item.as_dict()
        row["winner"] = {match.team_a.name: team_a, match.team_b.name: team_b}.get(row["winner"], row["winner"])
        maps.append(row)
    fresh = match.finished_at is None or now - match.finished_at <= FRESH_FOR
    try:
        with transaction.atomic():
            return MatchPost.objects.create(
                major=major,
                external_id=match.id,
                stage_name=match.tournament_name[:120],
                round_name=round_name,
                team_a=team_a[:80],
                team_b=team_b[:80],
                score_a=match.score_a,
                score_b=match.score_b,
                winner=_display(match.winner, names)[:80] if match.winner else "",
                maps=maps,
                progress=progress[:255],
                finished_at=match.finished_at,
                status=MatchPost.Status.PENDING if fresh else MatchPost.Status.SKIPPED,
                error="" if fresh else "Матч закончился давно — пост не отправлялся автоматически.",
            )
    except IntegrityError:
        return None


def sync_major(major: Major, *, now=None) -> dict:
    """Records new finished matches of one Major and posts the fresh ones. Returns counters."""
    now = now or timezone.now()
    counts = {"new": 0, "posted": 0, "skipped": 0, "errors": 0}
    history = pandascore.finished_matches(major.pandascore_serie_id)
    known = set(MatchPost.objects.filter(external_id__in=[match.id for match in history]).values_list("external_id", flat=True))
    names = _team_names()
    for match in history:
        if match.id in known:
            continue
        post = _record(major, match, history, names, now=now)
        if post is None:
            continue
        counts["new"] += 1
        if post.status == MatchPost.Status.SKIPPED:
            counts["skipped"] += 1
            continue
        try:
            send_post(post)
            counts["posted"] += 1
        except MatchPostError:
            counts["errors"] += 1
    return counts


def active_majors(now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    return Major.objects.filter(
        match_posts_enabled=True,
        pandascore_serie_id__isnull=False,
        starts_at__lte=now + WINDOW_MARGIN,
        ends_on__gte=today - WINDOW_MARGIN,
    )


def sync_all(*, now=None) -> str:
    """Celery beat entry point."""
    if not pandascore.api_key():
        return "no_key"
    results = []
    for major in active_majors(now):
        try:
            counts = sync_major(major, now=now)
        except pandascore.PandaScoreError as exc:
            results.append(f"{major.slug}: {exc}")
            continue
        results.append(f"{major.slug}: " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return "; ".join(results) or "idle"
