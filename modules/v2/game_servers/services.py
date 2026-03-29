from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import GameServer, GameServerStatusCheck


@dataclass(frozen=True)
class ServerProbeResult:
    status: str
    checked_at: object
    latency_ms: int | None
    error: str

    @property
    def is_online(self) -> bool:
        return self.status == GameServer.Status.ONLINE


def get_status_cache_ttl_seconds() -> int:
    raw = getattr(settings, "GAME_SERVER_STATUS_CACHE_TTL_SECONDS", 30)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 30


def probe_game_server(server: GameServer, *, timeout: float | None = None) -> ServerProbeResult:
    checked_at = timezone.now()
    timeout = float(timeout if timeout is not None else getattr(settings, "GAME_SERVER_STATUS_TIMEOUT_SECONDS", 1.5))
    started = time.monotonic()

    try:
        with socket.create_connection((server.host, int(server.port)), timeout=timeout):
            latency_ms = max(int((time.monotonic() - started) * 1000), 1)
            return ServerProbeResult(
                status=GameServer.Status.ONLINE,
                checked_at=checked_at,
                latency_ms=latency_ms,
                error="",
            )
    except OSError as exc:
        return ServerProbeResult(
            status=GameServer.Status.OFFLINE,
            checked_at=checked_at,
            latency_ms=None,
            error=str(exc)[:255],
        )


@transaction.atomic
def refresh_game_server_status(server: GameServer, *, force: bool = False) -> tuple[GameServer, ServerProbeResult | None]:
    if not force and server.last_check_at is not None:
        age_seconds = (timezone.now() - server.last_check_at).total_seconds()
        if age_seconds < get_status_cache_ttl_seconds():
            return server, None

    locked = GameServer.objects.select_for_update().get(pk=server.pk)
    if not force and locked.last_check_at is not None:
        age_seconds = (timezone.now() - locked.last_check_at).total_seconds()
        if age_seconds < get_status_cache_ttl_seconds():
            return locked, None

    probe = probe_game_server(locked)
    locked.status = probe.status
    locked.last_check_at = probe.checked_at
    locked.last_error = probe.error
    locked.last_latency_ms = probe.latency_ms
    if probe.is_online:
        locked.last_seen_online_at = probe.checked_at

    locked.save(
        update_fields=[
            "status",
            "last_check_at",
            "last_error",
            "last_latency_ms",
            "last_seen_online_at",
            "updated_at",
        ]
    )
    GameServerStatusCheck.objects.create(
        server=locked,
        status=probe.status,
        latency_ms=probe.latency_ms,
        error=probe.error,
    )
    return locked, probe


def refresh_enabled_game_servers(*, force: bool = False) -> list[GameServer]:
    servers: list[GameServer] = []
    for server in GameServer.objects.filter(is_enabled=True).order_by("sort_order", "id"):
        refreshed, _probe = refresh_game_server_status(server, force=force)
        servers.append(refreshed)
    return servers
