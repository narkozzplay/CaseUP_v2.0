"""Connecting farm accounts (bots): trade link parsing, Steam profile lookup,
bulk import and connection health checks."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import FarmAccount, FarmAccountInventorySync
from .services import parse_trade_url

STEAM_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
STEAM_SUMMARIES_BATCH = 100
PROFILE_XML_LIMIT = 10
PROFILE_TIMEOUT_SECONDS = 5
BULK_IMPORT_LIMIT = 100

_LINE_SPLIT_RE = re.compile(r"\s*[;|\t]\s*")
_TRADE_URL_RE = re.compile(r"https?://\S*steamcommunity\.com/tradeoffer/new/\S+", re.IGNORECASE)


@dataclass(frozen=True)
class SteamPersona:
    steam_id64: str
    persona_name: str
    avatar_url: str


@dataclass(frozen=True)
class ConnectionCheck:
    code: str
    label: str
    state: str  # ok / warn / fail / idle
    hint: str = ""


@dataclass
class BulkImportResult:
    created: list[FarmAccount] = field(default_factory=list)
    skipped: list[tuple[int, str, str]] = field(default_factory=list)  # (line_no, line, reason)

    @property
    def created_count(self) -> int:
        return len(self.created)


def _steam_api_key() -> str:
    try:
        from modules.v2.system.services import get_external_service_integration

        key = (get_external_service_integration().steam_api_key or "").strip()
    except Exception:
        key = ""
    return key or str(getattr(settings, "SOCIAL_AUTH_STEAM_API_KEY", "") or "").strip()


def _http_get(url: str, *, timeout: float = PROFILE_TIMEOUT_SECONDS) -> str:
    request = Request(url, headers={"User-Agent": "CaseUP/farm-connect", "Accept-Language": "en;q=0.9,ru;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_personas_via_api(steam_ids: list[str], api_key: str) -> dict[str, SteamPersona]:
    personas: dict[str, SteamPersona] = {}
    for offset in range(0, len(steam_ids), STEAM_SUMMARIES_BATCH):
        chunk = steam_ids[offset : offset + STEAM_SUMMARIES_BATCH]
        query = urlencode({"key": api_key, "steamids": ",".join(chunk), "format": "json"})
        payload = json.loads(_http_get(f"{STEAM_SUMMARIES_URL}?{query}") or "{}")
        for player in payload.get("response", {}).get("players", []):
            steam_id = str(player.get("steamid") or "")
            if not steam_id:
                continue
            personas[steam_id] = SteamPersona(
                steam_id64=steam_id,
                persona_name=str(player.get("personaname") or "")[:120],
                avatar_url=str(player.get("avatarfull") or player.get("avatarmedium") or "")[:500],
            )
    return personas


def _fetch_persona_via_xml(steam_id64: str) -> SteamPersona | None:
    raw = _http_get(f"https://steamcommunity.com/profiles/{steam_id64}/?xml=1")
    root = ET.fromstring(raw)
    if root.tag != "profile":
        return None
    return SteamPersona(
        steam_id64=steam_id64,
        persona_name=(root.findtext("steamID") or "").strip()[:120],
        avatar_url=(root.findtext("avatarFull") or root.findtext("avatarMedium") or "").strip()[:500],
    )


def fetch_steam_personas(steam_ids: list[str]) -> dict[str, SteamPersona]:
    """Best effort: one batched Web API call when a key is configured,
    otherwise the public profile XML for a limited number of accounts."""
    unique_ids = [value for value in dict.fromkeys(steam_ids) if value]
    if not unique_ids:
        return {}

    api_key = _steam_api_key()
    if api_key:
        try:
            return _fetch_personas_via_api(unique_ids, api_key)
        except Exception:
            pass

    personas: dict[str, SteamPersona] = {}
    for steam_id in unique_ids[:PROFILE_XML_LIMIT]:
        try:
            persona = _fetch_persona_via_xml(steam_id)
        except Exception:
            continue
        if persona is not None:
            personas[steam_id] = persona
    return personas


def refresh_account_profiles(accounts) -> int:
    accounts = [account for account in accounts if account.steam_id64]
    personas = fetch_steam_personas([account.steam_id64 for account in accounts])
    now = timezone.now()
    updated = 0
    for account in accounts:
        persona = personas.get(account.steam_id64)
        if persona is None:
            continue
        account.persona_name = persona.persona_name
        account.avatar_url = persona.avatar_url
        account.profile_synced_at = now
        account.save(update_fields=["persona_name", "avatar_url", "profile_synced_at", "updated_at"])
        updated += 1
    return updated


def parse_account_line(line: str) -> tuple[str, str]:
    """Returns (title, trade_url) from a line like `Bot 01; https://...`,
    `https://... | Bot 01` or a bare trade link."""
    value = (line or "").strip()
    match = _TRADE_URL_RE.search(value)
    if not match:
        raise ValueError("В строке нет трейд-ссылки Steam.")
    trade_url = match.group(0).rstrip(";|,")
    rest = (value[: match.start()] + " " + value[match.end() :]).strip()
    title = " ".join(part for part in _LINE_SPLIT_RE.split(rest) if part).strip(" ;|,-")
    return title[:120], trade_url


def next_account_title(owner, *, offset: int = 0) -> str:
    return f"Bot {FarmAccount.objects.filter(owner=owner).count() + offset + 1:02d}"


def find_duplicate_account(owner, steam_id64: str, *, exclude_id: int | None = None) -> FarmAccount | None:
    if not steam_id64:
        return None
    qs = FarmAccount.objects.filter(owner=owner, steam_id64=steam_id64)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.first()


def import_accounts(*, owner, text: str, role: str = FarmAccount.Role.FARM, status: str = FarmAccount.Status.ACTIVE) -> BulkImportResult:
    result = BulkImportResult()
    seen_ids: set[str] = set()
    lines = [(index, raw.strip()) for index, raw in enumerate((text or "").splitlines(), start=1) if raw.strip()]

    for line_no, line in lines[:BULK_IMPORT_LIMIT]:
        try:
            title, trade_url = parse_account_line(line)
            parsed = parse_trade_url(trade_url)
        except ValueError as exc:
            result.skipped.append((line_no, line, str(exc)))
            continue
        if parsed.steam_id64 in seen_ids:
            result.skipped.append((line_no, line, "Повтор в списке."))
            continue
        duplicate = find_duplicate_account(owner, parsed.steam_id64)
        if duplicate is not None:
            result.skipped.append((line_no, line, f"Уже подключён как «{duplicate.title}»."))
            continue
        seen_ids.add(parsed.steam_id64)
        with transaction.atomic():
            account = FarmAccount.objects.create(
                owner=owner,
                title=title or next_account_title(owner),
                steam_id64=parsed.steam_id64,
                trade_url=trade_url,
                role=role,
                status=status,
                sort_order=FarmAccount.objects.filter(owner=owner).count() + 1,
            )
        result.created.append(account)

    for line_no, line in lines[BULK_IMPORT_LIMIT:]:
        result.skipped.append((line_no, line, f"Лимит {BULK_IMPORT_LIMIT} аккаунтов за раз."))

    if result.created:
        try:
            refresh_account_profiles(result.created)
        except Exception:
            pass
    return result


def build_connection_checks(account: FarmAccount, latest_sync: FarmAccountInventorySync | None = None) -> list[ConnectionCheck]:
    checks: list[ConnectionCheck] = []

    trade_ok = False
    if account.trade_url:
        try:
            parsed = parse_trade_url(account.trade_url)
        except ValueError as exc:
            checks.append(ConnectionCheck("trade_url", "Трейд-ссылка", "fail", str(exc)))
        else:
            trade_ok = parsed.steam_id64 == account.steam_id64 or not account.steam_id64
            checks.append(
                ConnectionCheck(
                    "trade_url",
                    "Трейд-ссылка",
                    "ok" if trade_ok else "fail",
                    "Нужна для синхронизации и будущей авто-отправки." if trade_ok else "SteamID не совпадает с трейд-ссылкой.",
                )
            )
    else:
        checks.append(ConnectionCheck("trade_url", "Трейд-ссылка", "fail", "Добавь трейд-ссылку аккаунта."))

    if account.persona_name or account.avatar_url:
        checks.append(ConnectionCheck("profile", "Профиль Steam", "ok", account.persona_name))
    elif account.steam_id64:
        checks.append(ConnectionCheck("profile", "Профиль Steam", "idle", "Имя и аватар ещё не подтянуты."))
    else:
        checks.append(ConnectionCheck("profile", "Профиль Steam", "fail", "Нет SteamID."))

    if latest_sync is None:
        checks.append(ConnectionCheck("inventory", "Инвентарь", "idle", "Синхронизация ещё не запускалась."))
    elif latest_sync.status == FarmAccountInventorySync.Status.SUCCESS:
        checks.append(
            ConnectionCheck(
                "inventory",
                "Инвентарь",
                "ok",
                f"{latest_sync.total_assets} предм. · {timezone.localtime(latest_sync.started_at):%d.%m %H:%M}",
            )
        )
    elif latest_sync.status == FarmAccountInventorySync.Status.FAILED:
        checks.append(ConnectionCheck("inventory", "Инвентарь", "fail", latest_sync.error_text or "Синхронизация не удалась."))
    else:
        checks.append(ConnectionCheck("inventory", "Инвентарь", "warn", "Синхронизация в процессе."))

    return checks


def connection_state(checks: list[ConnectionCheck]) -> str:
    states = {check.state for check in checks}
    if "fail" in states:
        return "fail"
    if states <= {"ok"}:
        return "ok"
    return "warn"
