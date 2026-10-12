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


A2S_INFO_REQUEST = b"\xff\xff\xff\xffTSource Engine Query\x00"
A2S_HEADER = b"\xff\xff\xff\xff"
A2S_CHALLENGE = 0x41  # 'A'
A2S_INFO_RESPONSE = 0x49  # 'I'


def _query_a2s_info(host: str, port: int, timeout: float) -> None:
    """Send a Source A2S_INFO query over UDP; raise OSError if the server does not answer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        address = (socket.gethostbyname(host), port)
        sock.sendto(A2S_INFO_REQUEST, address)
        data, _ = sock.recvfrom(4096)
        if data[:4] == A2S_HEADER and len(data) >= 9 and data[4] == A2S_CHALLENGE:
            # CS2 servers answer with a challenge first; repeat the query with it appended.
            sock.sendto(A2S_INFO_REQUEST + data[5:9], address)
            data, _ = sock.recvfrom(4096)
        if data[:4] != A2S_HEADER or len(data) < 5 or data[4] != A2S_INFO_RESPONSE:
            raise OSError("Unexpected A2S_INFO response")


def probe_game_server(server: GameServer, *, timeout: float | None = None) -> ServerProbeResult:
    checked_at = timezone.now()
    timeout = float(timeout if timeout is not None else getattr(settings, "GAME_SERVER_STATUS_TIMEOUT_SECONDS", 1.5))
    started = time.monotonic()

    try:
        # CS2 game servers speak UDP (A2S); a plain TCP connect to the game port always fails.
        _query_a2s_info(server.host, int(server.port), timeout)
    except OSError as exc:
        return ServerProbeResult(
            status=GameServer.Status.OFFLINE,
            checked_at=checked_at,
            latency_ms=None,
            error=str(exc)[:255],
        )
    latency_ms = max(int((time.monotonic() - started) * 1000), 1)
    return ServerProbeResult(
        status=GameServer.Status.ONLINE,
        checked_at=checked_at,
        latency_ms=latency_ms,
        error="",
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
