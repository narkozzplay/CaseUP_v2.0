from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title, get_site_name

from .items import page_items
from .models import SWISS_PICK_LIMITS, BracketPick, Major, Match, RewardGrant, Stage, SwissPick, Team
from .services import PickemError, bracket_slots, feeders, current_major, eligibility, leaderboard, save_bracket_picks, save_swiss_picks, trade_url, user_summary

ROUND_LABELS = dict(Match.Round.choices)
SLOT_NAMES = {"win": "3-0", "advance": "advance", "lose": "0-3"}


def _base_template(request) -> str:
    return "layouts/base_dashboard.html" if request.user.is_authenticated else "majors/base_guest.html"


def _get_major(request, slug: str) -> Major:
    major = get_object_or_404(Major, slug=slug)
    if not major.is_published and not request.user.is_staff:
        raise Http404
    return major


def _render(request, major: Major, *, template: str, context: dict, title: str):
    payload = {"major": major, "majors_base_template": _base_template(request), "countdown": _countdown(major), "mj_tab": template}
    payload.update(context)
    return render_dashboard_page(
        request,
        full_template=f"majors/{template}.html",
        partial_template=f"majors/partials/{template}_content.html",
        context=payload,
        page_title=title,
        active_menu_key="majors",
    )


def _countdown(major: Major) -> dict | None:
    """What the timer counts down to: the opening day, then the lock of the next stage's picks."""
    now = timezone.now()
    if major.starts_at > now:
        at, label = major.starts_at, "До старта мейджора"
    else:
        upcoming = major.stages.filter(starts_at__gt=now).order_by("starts_at").first()
        if upcoming is None:
            return None
        at, label = upcoming.starts_at, f"До закрытия прогнозов: {upcoming.name}"
    left = int((at - now).total_seconds())
    return {
        "at": at, "label": label,
        "days": left // 86400, "hours": left % 86400 // 3600, "minutes": left % 3600 // 60, "seconds": left % 60,
    }


def _stages(major: Major) -> list[dict]:
    rows = []
    for stage in major.stages.prefetch_related("entries__team").order_by("order"):
        row = {"stage": stage, "entries": list(stage.entries.all())}
        if not stage.is_swiss:
            row["rounds"] = _actual_bracket(stage)
        rows.append(row)
    return rows


def _actual_bracket(stage: Stage) -> list[dict]:
    matches = {(m.round, m.slot): m for m in stage.matches.select_related("team_a", "team_b", "winner")}
    rounds: dict[int, list] = {}
    for key in bracket_slots(stage):
        rounds.setdefault(key[0], []).append({"round": key[0], "slot": key[1], "match": matches.get(key)})
    return [{"round": number, "label": ROUND_LABELS[number], "slots": slots} for number, slots in rounds.items()]


def _seo(request, major: Major) -> dict:
    canonical = request.build_absolute_uri(major.get_absolute_url())
    title = f"{major.name}: Pick'em, команды и расписание | {get_site_name()}"
    description = (
        f"{major.name}: {major.starts_at:%d.%m} – {major.ends_on:%d.%m.%Y}, {major.location}. "
        "Сделайте прогнозы на все стадии мейджора CS2 и получите награды за угаданных чемпиона, финалистов и полуфиналистов."
    )
    event = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": major.name,
        "url": canonical,
        "startDate": major.starts_at.date().isoformat(),
        "endDate": major.ends_on.isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "sport": "Counter-Strike 2",
    }
    if major.venue or major.location:
        event["location"] = {"@type": "Place", "name": major.venue or major.location, "address": major.location}
    image = request.build_absolute_uri(major.banner.url) if major.banner else ""
    if image:
        event["image"] = image
    text = json.dumps(event, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return {"seo_title": title, "seo_description": description, "seo_canonical": canonical, "seo_image": image, "seo_jsonld": mark_safe(text)}


def major_index(request):
    major = current_major()
    if major is None:
        raise Http404
    return redirect(major.get_absolute_url())


def major_detail(request, slug):
    major = _get_major(request, slug)
    summary = user_summary(major, request.user)
    context = {
        "stages": _stages(major),
        "rules": major.reward_rules.filter(is_active=True).select_related("plan", "stage", "item"),
        "summary": summary,
        # The short table appears once the first results make a difference.
        "top": [row for row in leaderboard(major, limit=5) if row["summary"].correct],
        "items": page_items(major),
        **_seo(request, major),
    }
    return _render(request, major, template="detail", context=context, title=build_page_title(major.name))


def _pick_stage(major: Major, request) -> Stage | None:
    stages = list(major.stages.order_by("order"))
    if not stages:
        return None
    wanted = request.GET.get("stage") or request.POST.get("stage")
    if wanted and wanted.isdigit():
        for stage in stages:
            if stage.order == int(wanted):
                return stage
    # By default the first stage still open whose teams are known, else the last one started.
    for stage in stages:
        if not stage.is_locked and (stage.entries.exists() or stage.matches.exists()):
            return stage
    started = [stage for stage in stages if stage.is_locked]
    return started[-1] if started else stages[0]


def _swiss_form(request, stage: Stage) -> dict:
    picks = dict(SwissPick.objects.filter(user=request.user, stage=stage).values_list("team_id", "pick"))
    entries = list(stage.entries.select_related("team"))
    return {
        "entries": [{"entry": entry, "team": entry.team, "pick": picks.get(entry.team_id, "")} for entry in entries],
        # Template-friendly names: 3-0 and 0-3 cannot be looked up with a dot.
        "limits": {name: SWISS_PICK_LIMITS[key] for name, key in SLOT_NAMES.items()},
        "counts": {name: sum(1 for value in picks.values() if value == key) for name, key in SLOT_NAMES.items()},
    }


def _bracket_form(request, stage: Stage) -> dict:
    """The bracket to fill: quarterfinals offer the real teams, later rounds the user's own picks."""
    picks = {(r, s): team for r, s, team in BracketPick.objects.filter(user=request.user, stage=stage).values_list("round", "slot", "team_id")}
    matches = {(m.round, m.slot): m for m in stage.matches.select_related("team_a", "team_b", "winner")}
    team_ids = set(picks.values())
    for match in matches.values():
        team_ids.update(pk for pk in (match.team_a_id, match.team_b_id) if pk)
    teams = {team.pk: team for team in Team.objects.filter(pk__in=team_ids)}

    def options_for(key):
        """[(source attribute, source key, team)] for the two sides of a match."""
        round_no, _slot = key
        if round_no == Match.Round.QUARTERFINAL:
            match = matches.get(key)
            return [("", "", match.team_a if match else None), ("", "", match.team_b if match else None)]
        if round_no == Match.Round.THIRD_PLACE:
            out = []
            for semi in feeders(*key):
                entrants = [picks.get(src) for src in feeders(*semi)]
                loser = next((pk for pk in entrants if pk and pk != picks.get(semi)), None) if picks.get(semi) else None
                out.append(("loser", f"r{semi[0]}s{semi[1]}", teams.get(loser)))
            return out
        return [("winner", f"r{src[0]}s{src[1]}", teams.get(picks.get(src))) for src in feeders(*key)]

    rounds: dict[int, list] = {}
    for key in bracket_slots(stage):
        match = matches.get(key)
        pick = picks.get(key)
        rounds.setdefault(key[0], []).append({
            "round": key[0], "slot": key[1], "key": f"r{key[0]}s{key[1]}", "match": match,
            "options": [{"source": source, "source_key": source_key, "team": team, "checked": bool(team and team.pk == pick)} for source, source_key, team in options_for(key)],
            "result": ("correct" if match.winner_id == pick else "wrong") if match and match.winner_id and pick else "",
        })
    bracket_teams = {str(pk): {"name": team.name, "logo": team.logo_url} for pk, team in teams.items()}
    return {
        "rounds": [{"round": number, "label": ROUND_LABELS[number], "slots": slots} for number, slots in rounds.items()],
        "bracket_teams_json": mark_safe(json.dumps(bracket_teams, ensure_ascii=False).replace("<", "\\u003c")),
    }


@login_required
def pickem(request, slug):
    major = _get_major(request, slug)
    stage = _pick_stage(major, request)
    if request.method == "POST" and stage is not None:
        try:
            if stage.is_swiss:
                picks = {}
                for key, value in request.POST.items():
                    if key.startswith("team_") and key[5:].isdigit():
                        picks[int(key[5:])] = value
                saved = save_swiss_picks(request.user, stage, picks)
            else:
                picks = {}
                for key in bracket_slots(stage):
                    value = request.POST.get(f"r{key[0]}s{key[1]}", "")
                    if value.isdigit():
                        picks[key] = int(value)
                saved = save_bracket_picks(request.user, stage, picks)
        except PickemError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Прогноз на «{stage.name}» сохранён: {saved} из {_max_picks(stage)}.")
        return redirect(f"{reverse('majors:pickem', args=[major.slug])}?stage={stage.order}")

    context = {
        "stage": stage,
        "all_stages": list(major.stages.order_by("order")),
        "eligibility": eligibility(request.user, major),
        "summary": user_summary(major, request.user),
        "grants": RewardGrant.objects.filter(user=request.user, rule__major=major).select_related("rule__plan", "rule__item", "stage"),
        "trade_url": trade_url(request.user),
        "seo_robots": "noindex, follow",
    }
    if stage is not None:
        context.update(_swiss_form(request, stage) if stage.is_swiss else _bracket_form(request, stage))
        context["max_picks"] = _max_picks(stage)
    return _render(request, major, template="pickem", context=context, title=build_page_title(f"Pick'em · {major.name}"))


def _max_picks(stage: Stage) -> int:
    return sum(SWISS_PICK_LIMITS.values()) if stage.is_swiss else len(bracket_slots(stage))


def major_leaderboard(request, slug):
    major = _get_major(request, slug)
    context = {
        "board": leaderboard(major, limit=100),
        "stages": list(major.stages.order_by("order")),
        "summary": user_summary(major, request.user),
        "seo_title": f"Таблица лидеров Pick'em · {major.name} | {get_site_name()}",
        "seo_description": f"Лучшие прогнозисты {major.name} на {get_site_name()}: верные прогнозы по стадиям мейджора CS2.",
        "seo_canonical": request.build_absolute_uri(reverse("majors:leaderboard", args=[major.slug])),
    }
    return _render(request, major, template="leaderboard", context=context, title=build_page_title(f"Лидеры Pick'em · {major.name}"))
