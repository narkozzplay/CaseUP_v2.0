"""Daily backups that leave the server.

Every night (Celery beat, `system.daily_backup`) the database and media are dumped into BACKUP_ROOT
(the `backup_data` volume), copies older than BACKUP_LOCAL_KEEP_DAYS are removed, and — when
BACKUP_SFTP_HOST is configured — the new copy is uploaded to the reg.ru hosting over SFTP (main hosting
account) or FTPS (BACKUP_PROTOCOL=ftps: a separate FTP user that sees only its own folder), where copies
older than BACKUP_REMOTE_KEEP_DAYS are removed too. The outcome is written to BACKUP_ROOT/last_backup.json
(Control Center → Система shows it) and a failure is mailed to ADMINS.

The SFTP server key (and an FTPS certificate not signed by a known CA) is pinned on first connect
(BACKUP_ROOT/.sftp_known_hosts) or up front with BACKUP_SFTP_HOST_KEY ("SHA256:…", printed by
`manage.py backup_offsite --test`).
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


# --------------------------------------------------------------------------- SFTP / FTPS


def _fingerprint(key) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def protocol() -> str:
    return "ftps" if (getattr(settings, "BACKUP_PROTOCOL", "") or "").strip().lower() == "ftps" else "sftp"


def _known_hosts() -> tuple[Path, dict[str, str]]:
    known_file = resolve_backup_root() / KNOWN_HOSTS_FILE
    known = {}
    if known_file.exists():
        for line in known_file.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                known[parts[0]] = parts[1]
    return known_file, known


def _check_pinned(host_id: str, fingerprint: str, *, what: str) -> None:
    """Pinned BACKUP_SFTP_HOST_KEY, or trust on first use (remembered in .sftp_known_hosts)."""
    pinned = (getattr(settings, "BACKUP_SFTP_HOST_KEY", "") or "").strip()
    known_file, known = _known_hosts()
    expected = pinned or known.get(host_id)
    if expected and expected != fingerprint:
        raise BackupError(f"{what} изменился ({fingerprint}, ожидался {expected}). Если хостинг действительно сменил сервер — удалите {known_file.name} или обновите BACKUP_SFTP_HOST_KEY.")
    if not expected:
        known[host_id] = fingerprint
        known_file.write_text("".join(f"{k} {v}\n" for k, v in known.items()), encoding="utf-8")


def _connect():
    """Open a session to the backup host: (closer, client, fingerprint). The client offers
    stat/mkdir/put/rename/remove/listdir/rmdir/close the same way for SFTP and FTPS."""
    if protocol() == "ftps":
        return _connect_ftps()
    return _connect_sftp()


def _connect_sftp():
    import paramiko

    host = settings.BACKUP_SFTP_HOST
    port = int(getattr(settings, "BACKUP_SFTP_PORT", 22))
    transport = paramiko.Transport((host, port))
    transport.banner_timeout = 30
    try:
        transport.start_client(timeout=30)
        fingerprint = _fingerprint(transport.get_remote_server_key())
        _check_pinned(f"{host}:{port}", fingerprint, what="Ключ SFTP-сервера")
        transport.auth_password(settings.BACKUP_SFTP_USER, getattr(settings, "BACKUP_SFTP_PASSWORD", ""))
        if not transport.is_authenticated():
            raise BackupError("Хостинг: неверный логин или пароль.")
        return transport, paramiko.SFTPClient.from_transport(transport), fingerprint
    except Exception:
        transport.close()
        raise


# --------------------------------------------------------------------------- FTPS (FTP over TLS)


class _FtpsClient:
    """SFTPClient-like wrapper over ftplib.FTP_TLS (explicit TLS, encrypted control and data)."""

    def __init__(self, ftp):
        self.ftp = ftp
        self.home = ftp.pwd()

    @staticmethod
    def _wrap(func, *args):
        import ftplib

        try:
            return func(*args)
        except (ftplib.error_perm, ftplib.error_temp) as exc:
            raise OSError(str(exc)) from exc

    def stat(self, path):
        from types import SimpleNamespace

        try:
            size = self._wrap(self.ftp.size, path)
            if size is not None:
                return SimpleNamespace(st_size=int(size))
        except OSError:
            pass
        self._wrap(self.ftp.cwd, path)  # a directory?
        self.ftp.cwd(self.home)
        return SimpleNamespace(st_size=0)

    def mkdir(self, path):
        self._wrap(self.ftp.mkd, path)

    def put(self, local, remote):
        with open(local, "rb") as handle:
            self._wrap(self.ftp.storbinary, f"STOR {remote}", handle)

    def rename(self, old, new):
        self._wrap(self.ftp.rename, old, new)

    def remove(self, path):
        self._wrap(self.ftp.delete, path)

    def listdir(self, path):
        names = self._wrap(self.ftp.nlst, path)
        return sorted({posixpath.basename(name.rstrip("/")) for name in names} - {"", ".", ".."})

    def rmdir(self, path):
        self._wrap(self.ftp.rmd, path)

    def close(self):
        try:
            self.ftp.quit()
        except Exception:  # noqa: BLE001
            self.ftp.close()


def _ftps_class():
    import ftplib

    class SessionReusingFTPS(ftplib.FTP_TLS):
        """Many FTP servers (ProFTPD, vsftpd) require the data connection to reuse the TLS session
        of the control connection; plain ftplib does not, and uploads then fail with 425/522."""

        def ntransfercmd(self, cmd, rest=None):
            conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
            if self._prot_p:
                conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
            return conn, size

    return SessionReusingFTPS


def _cert_fingerprint(der: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")


def _connect_ftps():
    import ssl

    host = settings.BACKUP_SFTP_HOST
    port = int(getattr(settings, "BACKUP_SFTP_PORT", 21))
    pinned = bool((getattr(settings, "BACKUP_SFTP_HOST_KEY", "") or "").strip())

    def open_session(context):
        ftp = _ftps_class()(context=context, timeout=60)
        ftp.connect(host, port)
        ftp.auth()
        return ftp

    ftp = None
    verified = False
    if not pinned:
        try:
            ftp = open_session(ssl.create_default_context())
            verified = True
        except ssl.SSLCertVerificationError:
            ftp = None  # self-signed / unknown CA: fall back to pinning the certificate below
    if ftp is None:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        ftp = open_session(context)
    try:
        fingerprint = _cert_fingerprint(ftp.sock.getpeercert(binary_form=True))
        if not verified:
            _check_pinned(f"ftps://{host}:{port}", fingerprint, what="Сертификат FTPS-сервера")
        ftp.login(settings.BACKUP_SFTP_USER, getattr(settings, "BACKUP_SFTP_PASSWORD", ""))
        ftp.prot_p()
        client = _FtpsClient(ftp)
        return client, client, fingerprint
    except Exception:
        ftp.close()
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
                raise BackupError(f"Хостинг: файл {file.name} загрузился не полностью.")
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
        return {"state": "warn", "text": "Бэкап есть только на этом сервере (отправка на хостинг не настроена)", "status": status, "finished": finished}
    return {"state": "ok", "text": f"Сохранён на хостинге: {status.get('remote_path', '')}", "status": status, "finished": finished}
