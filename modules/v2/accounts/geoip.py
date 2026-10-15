"""City/country by IP for the sessions list.

Uses the free "IP to City Lite" database from DB-IP (https://db-ip.com, CC BY 4.0): an .mmdb file
that the server downloads itself once a month (accounts.update_geoip task / `manage.py update_geoip`).
Without the file every lookup simply returns empty strings, so nothing else depends on it.
"""
from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import shutil
import tempfile
import threading
from datetime import date
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DOWNLOAD_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
ATTRIBUTION_URL = "https://db-ip.com"

_lock = threading.Lock()
_reader = None
_reader_mtime: float | None = None


def database_path() -> Path:
    return Path(getattr(settings, "GEOIP_DATABASE_PATH", Path(settings.BASE_DIR) / "var" / "geoip" / "dbip-city-lite.mmdb"))


def _get_reader():
    """Open the database once and reopen it after the monthly update replaced the file."""
    global _reader, _reader_mtime
    path = database_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _reader is None or _reader_mtime != mtime:
            try:
                import maxminddb

                _reader = maxminddb.open_database(str(path))
                _reader_mtime = mtime
            except Exception:  # noqa: BLE001 - a broken file must not break logins
                logger.exception("GeoIP database at %s could not be opened", path)
                _reader = None
        return _reader


def _name(node: dict | None) -> str:
    names = (node or {}).get("names") or {}
    return str(names.get("ru") or names.get("en") or "")


def lookup(ip: str | None) -> tuple[str, str]:
    """Return (city, country) for an IP, or empty strings when unknown."""
    if not ip:
        return "", ""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "", ""
    if address.is_private or address.is_loopback or address.is_link_local:
        return "Локальная сеть", ""
    reader = _get_reader()
    if reader is None:
        return "", ""
    try:
        record = reader.get(str(address)) or {}
    except Exception:  # noqa: BLE001
        logger.warning("GeoIP lookup failed for %s", ip, exc_info=True)
        return "", ""
    return _name(record.get("city"))[:120], _name(record.get("country"))[:120]


def _candidate_months(today: date) -> list[str]:
    previous = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
    return [today.strftime("%Y-%m"), previous.strftime("%Y-%m")]


def update_database(*, today: date | None = None, max_age_days: int = 25, force: bool = False) -> str:
    """Download this month's DB-IP City Lite file (falls back to last month's). Returns a status line."""
    path = database_path()
    if not force and path.exists():
        age_days = (date.today() - date.fromtimestamp(path.stat().st_mtime)).days
        if age_days < max_age_days:
            return f"up to date ({age_days} days old)"

    path.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for month in _candidate_months(today or date.today()):
        url = DOWNLOAD_URL.format(month=month)
        try:
            with requests.get(url, stream=True, timeout=60) as response:
                if response.status_code != 200:
                    errors.append(f"{month}: HTTP {response.status_code}")
                    continue
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".mmdb") as tmp:
                    with gzip.GzipFile(fileobj=response.raw) as unpacked:
                        shutil.copyfileobj(unpacked, tmp)
                    tmp_path = Path(tmp.name)
        except (requests.RequestException, OSError) as exc:
            errors.append(f"{month}: {exc.__class__.__name__}")
            continue

        try:
            import maxminddb

            maxminddb.open_database(str(tmp_path)).close()  # refuse a truncated/corrupt file
        except Exception as exc:  # noqa: BLE001
            tmp_path.unlink(missing_ok=True)
            errors.append(f"{month}: invalid file ({exc.__class__.__name__})")
            continue
        os.replace(tmp_path, path)
        return f"downloaded {month}"
    raise RuntimeError("GeoIP download failed: " + "; ".join(errors))
