from __future__ import annotations

import io
import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from common.logging import ThrottledAdminEmailHandler
from modules.v2.control_center.services import health

from . import backups


class FakeSFTP:
    """In-memory stand-in for paramiko.SFTPClient."""

    def __init__(self, files=None):
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set()
        for path in self.files:
            parts = path.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                self.dirs.add("/".join(parts[:i]))

    def stat(self, path):
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.dirs:
            return SimpleNamespace(st_size=0)
        raise OSError(path)

    def mkdir(self, path):
        self.dirs.add(path)

    def put(self, local, remote):
        self.files[remote] = Path(local).read_bytes()

    def rename(self, old, new):
        self.files[new] = self.files.pop(old)

    def remove(self, path):
        if path not in self.files:
            raise OSError(path)
        del self.files[path]

    def listdir(self, path):
        if path not in self.dirs:
            raise OSError(path)
        prefix = path + "/"
        names = {item[len(prefix):].split("/")[0] for item in list(self.files) + list(self.dirs) if item.startswith(prefix)}
        return sorted(names)

    def rmdir(self, path):
        self.dirs.discard(path)

    def close(self):
        pass


def fake_dumps(test):
    def db(*, destination):
        Path(destination).write_bytes(b"pg-dump")
        return destination

    def media(*, destination):
        Path(destination).write_bytes(b"media-tar")
        return destination

    return mock.patch.multiple(backups, backup_postgres_database=db, backup_media_directory=media)


class BackupTestCase(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        override = override_settings(BACKUP_ROOT=self.root, ADMINS=[("Admin", "admin@case-up.ru")])
        override.enable()
        self.addCleanup(override.disable)


class DailyBackupTests(BackupTestCase):
    def test_local_backup_without_sftp(self):
        with fake_dumps(self):
            report = backups.run_daily_backup()
        self.assertTrue(report.ok, report.error)
        self.assertFalse(report.uploaded)
        self.assertTrue((self.root / report.name / "database.sqlc").exists())
        self.assertEqual(backups.read_status()["name"], report.name)
        self.assertEqual(backups.status_summary()["state"], "warn")
        self.assertEqual(health.check_backups().status, health.WARN)

    def test_old_local_copies_are_removed(self):
        old = self.root / "20200101_000000"
        old.mkdir()
        manual = self.root / "before-upgrade"  # not a dated folder: never touched
        manual.mkdir()
        removed = backups.prune_local()
        self.assertEqual(removed, ["20200101_000000"])
        self.assertTrue(manual.exists())

    def test_failure_is_recorded_and_mailed(self):
        with mock.patch.object(backups, "backup_postgres_database", side_effect=RuntimeError("pg_dump: connection refused")):
            report = backups.run_daily_backup()
        self.assertFalse(report.ok)
        self.assertEqual(list(self.root.glob("2*")), [])  # half-made folder removed
        self.assertEqual(backups.status_summary()["state"], "fail")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("connection refused", mail.outbox[0].body)

    @override_settings(BACKUP_SFTP_HOST="backup.example", BACKUP_SFTP_USER="u123", BACKUP_SFTP_PATH="caseup-backups")
    def test_upload_and_remote_retention(self):
        old_name = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y%m%d_%H%M%S")
        sftp = FakeSFTP({f"caseup-backups/{old_name}/database.sqlc": b"old", "caseup-backups/notes.txt": b"keep"})
        with fake_dumps(self), mock.patch.object(backups, "_connect", return_value=(mock.Mock(), sftp, "SHA256:x")):
            report = backups.run_daily_backup()
        self.assertTrue(report.ok, report.error)
        self.assertTrue(report.uploaded)
        self.assertEqual(sftp.files[f"caseup-backups/{report.name}/database.sqlc"], b"pg-dump")
        self.assertEqual(report.remote_removed, [old_name])
        self.assertIn("caseup-backups/notes.txt", sftp.files)
        self.assertFalse(any(name.endswith(".part") for name in sftp.files))
        self.assertEqual(backups.status_summary()["state"], "ok")


@override_settings(BACKUP_SFTP_HOST="backup.example", BACKUP_SFTP_USER="u123", BACKUP_SFTP_PASSWORD="secret")
class HostKeyTests(BackupTestCase):
    def _transport(self, key_bytes: bytes):
        transport = mock.Mock()
        transport.get_remote_server_key.return_value = SimpleNamespace(asbytes=lambda: key_bytes)
        transport.is_authenticated.return_value = True
        return transport

    def _connect_with(self, key_bytes: bytes):
        with mock.patch("paramiko.Transport", return_value=self._transport(key_bytes)), mock.patch("paramiko.SFTPClient.from_transport", return_value=FakeSFTP()):
            return backups._connect()

    def test_key_is_pinned_on_first_connect_and_checked_later(self):
        _t, _s, fingerprint = self._connect_with(b"key-one")
        self.assertIn(fingerprint, (self.root / backups.KNOWN_HOSTS_FILE).read_text())
        self._connect_with(b"key-one")
        with self.assertRaisesMessage(backups.BackupError, "Ключ SFTP-сервера изменился"):
            self._connect_with(b"key-two")

    def test_explicit_fingerprint_wins(self):
        with override_settings(BACKUP_SFTP_HOST_KEY="SHA256:something-else"), self.assertRaises(backups.BackupError):
            self._connect_with(b"key-one")


class CommandTests(BackupTestCase):
    def test_test_flag_requires_configuration(self):
        from django.core.management import CommandError, call_command

        with self.assertRaisesMessage(CommandError, "SFTP is not configured"):
            call_command("backup_offsite", "--test")

    def test_now_runs_the_backup(self):
        from django.core.management import call_command

        out = io.StringIO()
        with fake_dumps(self):
            call_command("backup_offsite", "--now", stdout=out)
        self.assertIn("kept on this server only", out.getvalue())


class SeoTests(TestCase):
    def test_robots_and_sitemap(self):
        client = Client()
        robots = client.get("/robots.txt")
        self.assertEqual(robots["Content-Type"], "text/plain; charset=utf-8")
        self.assertContains(robots, "Disallow: /control-center/")
        self.assertContains(robots, "Sitemap: https://testserver/sitemap.xml")
        sitemap = client.get("/sitemap.xml")
        self.assertContains(sitemap, "<loc>https://testserver/</loc>")
        self.assertContains(sitemap, "/agreement/")
        self.assertNotContains(sitemap, "control-center")

    def test_link_preview_tags(self):
        response = Client().get("/agreement/")
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'property="og:image"')
        self.assertContains(response, 'rel="canonical" href="http://testserver/agreement/"')


@override_settings(ADMINS=[("Admin", "admin@case-up.ru")])
class ThrottledMailTests(SimpleTestCase):
    def test_same_error_is_mailed_once(self):
        cache.clear()
        handler = ThrottledAdminEmailHandler()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        for _ in range(3):
            record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: /cases/", None, exc_info)
            handler.emit(record)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_HOST="", ADMINS=[])
    def test_health_reports_missing_mail_settings(self):
        self.assertEqual(health.check_email().status, health.WARN)
