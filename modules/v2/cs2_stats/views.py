from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect

from modules.v2.profiles.models import UserProfile
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

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

    ctx = get_cs2_stats_context(request.user, viewer=request.user)
    return render_dashboard_page(
        request,
        full_template="cs2_stats/player_stats.html",
        partial_template="cs2_stats/partials/player_stats_content.html",
        context=ctx,
        page_title=build_page_title(f"CS2 — {ctx['display_name']}"),
        active_menu_key="cs2_stats",
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
