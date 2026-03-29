from __future__ import annotations

from django.shortcuts import get_object_or_404, render

from .models import GameServer
from .services import refresh_game_server_status


def server_list_view(request):
    servers = list(
        GameServer.objects.filter(is_enabled=True)
        .prefetch_related("privileges")
        .order_by("sort_order", "id")
    )
    refreshed = []
    online_total = 0
    for server in servers:
        current, _probe = refresh_game_server_status(server)
        refreshed.append(current)
        if current.status == GameServer.Status.ONLINE:
            online_total += 1

    context = {
        "servers": refreshed,
        "online_total": online_total,
        "servers_total": len(refreshed),
    }
    return render(request, "game_servers/list.html", context)


def server_detail_view(request, slug: str):
    server = get_object_or_404(
        GameServer.objects.prefetch_related("privileges", "status_checks"),
        slug=slug,
        is_enabled=True,
    )
    server, _probe = refresh_game_server_status(server)
    recent_checks = list(server.status_checks.all()[:6])
    privilege_groups = {
        "vip": [item for item in server.privileges.all() if item.privilege_type == item.PrivilegeType.VIP and item.is_active],
        "admin": [item for item in server.privileges.all() if item.privilege_type == item.PrivilegeType.ADMIN and item.is_active],
        "moderation": [item for item in server.privileges.all() if item.privilege_type == item.PrivilegeType.MODERATION and item.is_active],
    }
    context = {
        "server": server,
        "recent_checks": recent_checks,
        "privilege_groups": privilege_groups,
    }
    return render(request, "game_servers/detail.html", context)
