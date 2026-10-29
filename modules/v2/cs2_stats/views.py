from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .match_sync import MatchSyncResult, save_auth_code, sync_cs2_match_history
from .models import CS2_MAPS, CS2PlayerStats, Cs2MatchRecord, Cs2MatchSyncConfig, FACEITExtendedStats
from .services import get_cs2_stats_context, sync_cs2_steam_stats, sync_faceit_extended_stats


User = get_user_model()


@login_required
def cs2_stats_own_view(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "sync_steam":
            result = sync_cs2_steam_stats(request.user)
            if result.ok:
                messages.success(request, f"✓ {result.message}")
            else:
                messages.error(request, result.message)
        elif action == "sync_faceit":
            result = sync_faceit_extended_stats(request.user)
            if result.ok:
                messages.success(request, f"✓ {result.message}")
            else:
                messages.error(request, result.message)
        return redirect("cs2_stats:own")

    _auto_sync_if_empty(request.user)

    ctx = get_cs2_stats_context(request.user, viewer=request.user)
    return render_dashboard_page(
        request,
        full_template="cs2_stats/player_stats.html",
        partial_template="cs2_stats/partials/player_stats_content.html",
        context=ctx,
        page_title=build_page_title(f"CS2 — {ctx['display_name']}"),
        active_menu_key="cs2_stats",
    )


def _auto_sync_if_empty(user) -> None:
    """Silently trigger first-time sync when a user has no CS2 stats yet."""
    try:
        profile = user.profile_v2
    except Exception:
        return

    steam_id = (getattr(profile, "steam_id", None) or "").strip()
    faceit_id = (getattr(profile, "faceit_player_id", None) or "").strip()

    if steam_id and not CS2PlayerStats.objects.filter(user=user).exists():
        try:
            sync_cs2_steam_stats(user)
        except Exception:
            pass

    if faceit_id and not FACEITExtendedStats.objects.filter(user=user).exists():
        try:
            sync_faceit_extended_stats(user)
        except Exception:
            pass


@login_required
def cs2_match_history_view(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "sync_matches":
            result: MatchSyncResult = sync_cs2_match_history(request.user)
            if result.ok:
                messages.success(request, f"✓ {result.message}")
            else:
                messages.error(request, result.message)
        elif action == "save_auth_code":
            auth_code = (request.POST.get("auth_code") or "").strip()
            last_token = (request.POST.get("last_known_token") or "").strip()
            if auth_code:
                save_auth_code(request.user, auth_code, last_token)
                messages.success(request, "✓ Настройки сохранены.")
            else:
                messages.error(request, "Game Authentication Code не может быть пустым.")
        return redirect("cs2_stats:match_history")

    cfg = Cs2MatchSyncConfig.objects.filter(user=request.user).first()
    matches_qs = (
        Cs2MatchRecord.objects
        .filter(user=request.user)
        .prefetch_related("player_stats")
        .order_by("-played_at")[:50]
    )

    matches = []
    for m in matches_qs:
        map_info = CS2_MAPS.get(m.map_name, {})
        matches.append({
            "record": m,
            "map_display": m.map_display,
            "map_color": map_info.get("color", "#4A5568"),
            "own_stat": m.player_stats.filter(
                steam_id=getattr(request.user.profile_v2, "steam_id", "")
            ).first(),
        })

    ctx = {
        "sync_cfg": cfg,
        "matches": matches,
        "total_matches": Cs2MatchRecord.objects.filter(user=request.user).count(),
    }
    return render_dashboard_page(
        request,
        full_template="cs2_stats/match_history.html",
        partial_template="cs2_stats/partials/match_history_content.html",
        context=ctx,
        page_title=build_page_title("CS2 — Match History"),
        active_menu_key="cs2_match_history",
    )


@login_required
def cs2_match_detail_view(request, match_id: int):
    record = get_object_or_404(Cs2MatchRecord, id=match_id, user=request.user)
    player_stats = list(record.player_stats.all().order_by("-score"))

    owner_steam_id = getattr(request.user.profile_v2, "steam_id", "") or ""
    team_a = [p for p in player_stats if p.team == 2]
    team_b = [p for p in player_stats if p.team == 3]
    if not team_a and not team_b:
        team_a = player_stats[:5]
        team_b = player_stats[5:]

    map_info = CS2_MAPS.get(record.map_name, {})
    ctx = {
        "record": record,
        "map_display": record.map_display,
        "map_color": map_info.get("color", "#4A5568"),
        "map_color2": map_info.get("color2", "#2D3748"),
        "team_a": team_a,
        "team_b": team_b,
        "owner_steam_id": owner_steam_id,
    }
    return render_dashboard_page(
        request,
        full_template="cs2_stats/match_detail.html",
        partial_template="cs2_stats/partials/match_detail_content.html",
        context=ctx,
        page_title=build_page_title(f"Match — {record.map_display}"),
        active_menu_key="cs2_match_history",
    )


@login_required
def cs2_stats_user_view(request, user_id: int):
    target_user = get_object_or_404(User, pk=user_id, is_active=True)
    ctx = get_cs2_stats_context(target_user, viewer=request.user)
    return render_dashboard_page(
        request,
        full_template="cs2_stats/player_stats.html",
        partial_template="cs2_stats/partials/player_stats_content.html",
        context=ctx,
        page_title=build_page_title(f"CS2 — {ctx['display_name']}"),
        active_menu_key="cs2_stats",
    )


# ---------------------------------------------------------------------------
# Any player's stats, like csstats: search by Steam / FACEIT link
# ---------------------------------------------------------------------------

@login_required
def player_search_view(request):
    from .models import TrackedPlayer
    from .players import lookup_player

    query = (request.GET.get("q") or "").strip()
    error = ""
    if query:
        result = lookup_player(query, user=request.user)
        if result.ok:
            return redirect("cs2_stats:player", steam_id=result.player.steam_id)
        error = result.message

    ctx = {
        "query": query,
        "error": error,
        "recent_players": TrackedPlayer.objects.exclude(last_synced_at=None).order_by("-last_viewed_at")[:12],
        "top_players": TrackedPlayer.objects.filter(faceit_max_elo__gt=0).order_by("-faceit_max_elo")[:12],
        "players_total": TrackedPlayer.objects.count(),
    }
    return render_dashboard_page(
        request,
        full_template="cs2_stats/players.html",
        partial_template="cs2_stats/partials/players_content.html",
        context=ctx,
        page_title=build_page_title("CS2 — Поиск игрока"),
        active_menu_key="cs2_players",
    )


def _tracked_player_or_404(request, steam_id: str):
    from django.http import Http404

    from .players import is_steam_id64, open_player

    if not is_steam_id64(steam_id):
        raise Http404
    result = open_player(steam_id, user=request.user)
    if not result.ok:
        return None, result.message
    return result.player, ""


@login_required
def player_detail_view(request, steam_id: str):
    from .players import manual_refresh, player_page_context

    if request.method == "POST":
        from .models import TrackedPlayer

        player = get_object_or_404(TrackedPlayer, steam_id=steam_id)
        result = manual_refresh(player)
        (messages.success if result.ok else messages.error)(request, result.message)
        return redirect("cs2_stats:player", steam_id=steam_id)

    player, error = _tracked_player_or_404(request, steam_id)
    if player is None:
        messages.error(request, error)
        return redirect("cs2_stats:players")
    ctx = player_page_context(player)
    return render_dashboard_page(
        request,
        full_template="cs2_stats/player_detail.html",
        partial_template="cs2_stats/partials/player_detail_content.html",
        context=ctx,
        page_title=build_page_title(f"CS2 — {player.display_name}"),
        active_menu_key="cs2_players",
    )


@login_required
def player_matches_view(request, steam_id: str):
    from django.core.paginator import Paginator

    from .models import TrackedPlayer

    player = get_object_or_404(TrackedPlayer, steam_id=steam_id)
    page_obj = Paginator(player.faceit_matches.all(), 50).get_page(request.GET.get("page") or 1)
    ctx = {
        "player": player,
        "page_obj": page_obj,
        "matches": page_obj.object_list,
        "stored_matches": page_obj.paginator.count,
    }
    return render_dashboard_page(
        request,
        full_template="cs2_stats/player_matches.html",
        partial_template="cs2_stats/partials/player_matches_content.html",
        context=ctx,
        page_title=build_page_title(f"FACEIT — {player.display_name}"),
        active_menu_key="cs2_players",
    )
