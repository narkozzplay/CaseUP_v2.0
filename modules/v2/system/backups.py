"""Daily backups that leave the server.

Every night (Celery beat, `system.daily_backup`) the database and media are dumped into BACKUP_ROOT
(the `backup_data` volume), copies older than BACKUP_LOCAL_KEEP_DAYS are removed, and — when
BACKUP_SFTP_HOST is configured — the new copy is uploaded over SFTP (reg.ru hosting) where copies
older than BACKUP_REMOTE_KEEP_DAYS are removed too. The outcome is written to BACKUP_ROOT/last_backup.json
(Control Center → Система shows it) and a failure is mailed to ADMINS.

The SFTP server key is pinned on first connect (BACKUP_ROOT/.sftp_known_hosts) or up front with
BACKUP_SFTP_HOST_KEY ("SHA256:…", printed by `manage.py backup_offsite --test`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings

from .ops import backup_media_directory, backup_postgres_database, create_backup_paths, resolve_backup_root

logger = logging.getLogger(__name__)

STATUS_FILE = "last_backup.json"
KNOWN_HOSTS_FILE = ".sftp_known_hosts"
_DIR_RE = re.compile(r"^(\d{8}_\d{6})$")
_DIR_FORMAT = "%Y%m%d_%H%M%S"


class BackupError(Exception):
    pass


@dataclass
class BackupReport:
    name: str = ""
    started_at: str = ""
    finished_at: str = ""
    ok: bool = False
    size_bytes: int = 0
    uploaded: bool = False
    remote_path: str = ""
    local_removed: list[str] = field(default_factory=list)
    remote_removed: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# --------------------------------------------------------------------------- settings


def sftp_configured() -> bool:
    return bool(getattr(settings, "BACKUP_SFTP_HOST", "") and getattr(settings, "BACKUP_SFTP_USER", ""))


def _keep_days(name: str, default: int) -> int:
    return max(1, int(getattr(settings, name, default)))


# --------------------------------------------------------------------------- status


def read_status() -> dict | None:
    try:
        return json.loads((resolve_backup_root() / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_status(report: BackupReport) -> None:
    path = resolve_backup_root() / STATUS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- local


def _dir_time(name: str) -> datetime | None:
    if not _DIR_RE.match(name):
        return None
    return datetime.strptime(name, _DIR_FORMAT).replace(tzinfo=timezone.utc)


def prune_local(*, now: datetime | None = None, keep_days: int | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days or _keep_days("BACKUP_LOCAL_KEEP_DAYS", 7))
    removed = []
    for path in sorted(resolve_backup_root().iterdir()):
        stamp = _dir_time(path.name)
        if path.is_dir() and stamp is not None and stamp < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
    return removed


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


# --------------------------------------------------------------------------- SFTP


def _fingerprint(key) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _connect():
    """Open an SFTP session, checking the server key (pinned value or trust on first use)."""
    import paramiko

    host = settings.BACKUP_SFTP_HOST
    port = int(getattr(settings, "BACKUP_SFTP_PORT", 22))
    transport = paramiko.Transport((host, port))
    transport.banner_timeout = 30
    try:
        transport.start_client(timeout=30)
        server_key = transport.get_remote_server_key()
        fingerprint = _fingerprint(server_key)
        pinned = (getattr(settings, "BACKUP_SFTP_HOST_KEY", "") or "").strip()
        known_file = resolve_backup_root() / KNOWN_HOSTS_FILE
        known = {}
        if known_file.exists():
            for line in known_file.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2:
                    known[parts[0]] = parts[1]
        expected = pinned or known.get(f"{host}:{port}")
        if expected and expected != fingerprint:
            raise BackupError(f"Ключ SFTP-сервера изменился ({fingerprint}, ожидался {expected}). Если хостинг действительно сменил сервер — удалите {known_file.name} или обновите BACKUP_SFTP_HOST_KEY.")
        if not expected:
            known[f"{host}:{port}"] = fingerprint
            known_file.write_text("".join(f"{k} {v}\n" for k, v in known.items()), encoding="utf-8")
        transport.auth_password(settings.BACKUP_SFTP_USER, getattr(settings, "BACKUP_SFTP_PASSWORD", ""))
        if not transport.is_authenticated():
            raise BackupError("SFTP: неверный логин или пароль.")
        return transport, paramiko.SFTPClient.from_transport(transport), fingerprint
    except Exception:
        transport.close()
        raise


def _remote_base() -> str:
    return (getattr(settings, "BACKUP_SFTP_PATH", "") or "caseup-backups").rstrip("/") or "caseup-backups"


def _mkdirs(sftp, path: str) -> None:
    current = "/" if path.startswith("/") else ""
    for part in [p for p in path.split("/") if p]:
        current = posixpath.join(current, part) if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def upload(local_dir: Path) -> str:
    transport, sftp, _ = _connect()
    try:
        base = _remote_base()
        target = posixpath.join(base, local_dir.name)
        _mkdirs(sftp, target)
        for file in sorted(item for item in local_dir.iterdir() if item.is_file()):
            remote = posixpath.join(target, file.name)
            partial = remote + ".part"
            sftp.put(str(file), partial)
            if sftp.stat(partial).st_size != file.stat().st_size:
                raise BackupError(f"SFTP: файл {file.name} загрузился не полностью.")
            try:
                sftp.remove(remote)
            except OSError:
                pass
            sftp.rename(partial, remote)
        return target
    finally:
        sftp.close()
        transport.close()


def prune_remote(*, now: datetime | None = None, keep_days: int | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days or _keep_days("BACKUP_REMOTE_KEEP_DAYS", 30))
    transport, sftp, _ = _connect()
    removed = []
    try:
        base = _remote_base()
        try:
            names = sftp.listdir(base)
        except OSError:
            return removed
        for name in sorted(names):
            stamp = _dir_time(name)
            if stamp is None or stamp >= cutoff:
                continue
            folder = posixpath.join(base, name)
            for file in sftp.listdir(folder):
                sftp.remove(posixpath.join(folder, file))
            sftp.rmdir(folder)
            removed.append(name)
        return removed
    finally:
        sftp.close()
        transport.close()


def check_connection() -> dict:
    """For `backup_offsite --test`: connect, report the key fingerprint and what is stored remotely."""
    transport, sftp, fingerprint = _connect()
    try:
        base = _remote_base()
        try:
            entries = sorted(name for name in sftp.listdir(base) if _DIR_RE.match(name))
        except OSError:
            entries = []
        return {"fingerprint": fingerprint, "path": base, "backups": entries}
    finally:
        sftp.close()
        transport.close()


# --------------------------------------------------------------------------- the daily job


def run_daily_backup(*, upload_offsite: bool = True) -> BackupReport:
    report = BackupReport(started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        artifacts = create_backup_paths()
        report.name = artifacts.backup_dir.name
        try:
            backup_postgres_database(destination=artifacts.db_dump_path)
            backup_media_directory(destination=artifacts.media_archive_path)
        except Exception:
            shutil.rmtree(artifacts.backup_dir, ignore_errors=True)
            raise
        report.size_bytes = _dir_size(artifacts.backup_dir)
        if upload_offsite and sftp_configured():
            report.remote_path = upload(artifacts.backup_dir)
            report.uploaded = True
            report.remote_removed = prune_remote()
        report.local_removed = prune_local()
        report.ok = True
    except Exception as exc:  # noqa: BLE001 - reported below, the task itself must not crash the worker loop
        report.error = f"{exc.__class__.__name__}: {exc}"[:500]
        logger.exception("Daily backup failed")
    report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_status(report)
    if not report.ok:
        _mail_failure(report)
    return report


def _mail_failure(report: BackupReport) -> None:
    from django.core.mail import mail_admins

    try:
        mail_admins(
            "Бэкап не удался",
            f"Ежедневный бэкап завершился ошибкой.\n\n{report.error}\n\nНачало: {report.started_at}\nПроверьте: dc logs --tail=200 celery_worker",
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not mail the backup failure")


def status_summary(*, now: datetime | None = None) -> dict:
    """For the Control Center health page."""
    status = read_status()
    now = now or datetime.now(timezone.utc)
    if not status:
        return {"state": "warn", "text": "Автоматических бэкапов ещё не было", "status": None}
    try:
        finished = datetime.fromisoformat(status.get("finished_at", ""))
    except ValueError:
        finished = None
    if not status.get("ok"):
        return {"state": "fail", "text": f"Последний бэкап с ошибкой: {status.get('error', '')}", "status": status, "finished": finished}
    if finished is None or now - finished > timedelta(hours=30):
        return {"state": "warn", "text": "Последнему бэкапу больше суток", "status": status, "finished": finished}
    if not status.get("uploaded"):
        return {"state": "warn", "text": "Бэкап есть только на этом сервере (SFTP не настроен)", "status": status, "finished": finished}
    return {"state": "ok", "text": f"Сохранён на хостинге: {status.get('remote_path', '')}", "status": status, "finished": finished}
