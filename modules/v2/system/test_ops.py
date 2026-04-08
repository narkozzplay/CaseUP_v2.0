from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.system.models import ModuleState, SiteConfiguration
from modules.v2.system.module_registry import MODULE_REGISTRY
from modules.v2.system.ops import (
    backup_media_directory,
    bootstrap_system_state,
    create_backup_paths,
    grant_role,
    resolve_restore_artifacts,
    validate_critical_prod_env,
)

User = get_user_model()


@contextmanager
def workspace_temp_dir():
    root = Path.cwd() / ".tmp_test_ops"
    root.mkdir(parents=True, exist_ok=True)
    temp_dir = root / f"caseup_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class BootstrapOpsTests(TestCase):
    def test_bootstrap_system_state_creates_singleton_and_module_states(self):
        self.assertEqual(SiteConfiguration.objects.count(), 0)
        self.assertEqual(ModuleState.objects.count(), 0)

        result = bootstrap_system_state(sync_defaults=True)

        self.assertTrue(result.created_site_configuration)
        self.assertEqual(set(result.created_module_state_keys), set(MODULE_REGISTRY))
        self.assertEqual(SiteConfiguration.objects.count(), 1)
        self.assertEqual(ModuleState.objects.count(), len(MODULE_REGISTRY))

    def test_bootstrap_system_state_fills_blank_defaults_without_overwriting_existing_values(self):
        config = SiteConfiguration.objects.create(
            site_name="Prod Name",
            support_email="",
            public_base_url="",
        )
        ModuleState.objects.create(key="steam_catalog", is_enabled=False)

        result = bootstrap_system_state(sync_defaults=True)
        config.refresh_from_db()

        self.assertFalse(result.created_site_configuration)
        self.assertTrue(result.updated_site_configuration)
        self.assertEqual(config.site_name, "Prod Name")
        self.assertNotEqual(config.support_email, None)
        self.assertEqual(ModuleState.objects.count(), len(MODULE_REGISTRY))


class GrantRoleOpsTests(TestCase):
    def test_grant_role_service_synchronizes_profile_and_django_flags(self):
        user = User.objects.create_user(username="ops_admin", password="StrongPass123")

        result = grant_role(username="ops_admin", role=UserRole.ADMIN)
        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)

        self.assertEqual(result.role, UserRole.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(profile.role, UserRole.ADMIN)

    def test_grant_role_command_sets_super_admin(self):
        user = User.objects.create_user(username="chief", password="StrongPass123")

        call_command("grant_role", "chief", UserRole.SUPER_ADMIN)
        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(profile.role, UserRole.SUPER_ADMIN)

    @patch.dict(
        os.environ,
        {
            "DJANGO_SUPERUSER_USERNAME": "root",
            "DJANGO_SUPERUSER_EMAIL": "root@example.com",
            "DJANGO_SUPERUSER_PASSWORD": "StrongPass123",
        },
        clear=False,
    )
    def test_bootstrap_prod_can_create_superuser_from_env(self):
        call_command("bootstrap_prod", "--skip-env-check", "--create-superuser-from-env")

        user = User.objects.get(username="root")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.profile_v2.role, UserRole.SUPER_ADMIN)


class ValidationOpsTests(TestCase):
    @patch.dict(
        os.environ,
        {
            "DJANGO_SECRET_KEY": "secret",
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DB_ENGINE": "postgres",
            "POSTGRES_HOST": "db",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "caseup",
            "POSTGRES_USER": "caseup",
            "POSTGRES_PASSWORD": "caseup",
            "CELERY_BROKER_URL": "redis://redis:6379/0",
        },
        clear=True,
    )
    def test_validate_critical_prod_env_accepts_complete_postgres_setup(self):
        validate_critical_prod_env()

    @patch.dict(os.environ, {"DB_ENGINE": "sqlite"}, clear=True)
    def test_validate_critical_prod_env_rejects_non_postgres(self):
        with self.assertRaises(CommandError):
            validate_critical_prod_env()

    def test_restore_db_requires_explicit_confirmation(self):
        with self.assertRaises(CommandError):
            call_command("restore_db", "--backup-dir", "does_not_matter")


class BackupOpsTests(TestCase):
    def test_create_backup_paths_uses_configured_root(self):
        with workspace_temp_dir() as tmp_dir:
            with override_settings(BACKUP_ROOT=tmp_dir):
                artifacts = create_backup_paths(output_dir_name="unit_case", include_db=True, include_media=False)

            self.assertEqual(artifacts.backup_dir, tmp_dir / "unit_case")
            self.assertIsNotNone(artifacts.db_dump_path)
            self.assertIsNone(artifacts.media_archive_path)

    def test_backup_media_directory_creates_tarball(self):
        with workspace_temp_dir() as tmp_dir:
            media_root = tmp_dir / "media"
            media_root.mkdir(parents=True)
            (media_root / "example.txt").write_text("hello", encoding="utf-8")
            archive_path = tmp_dir / "media.tar.gz"

            with override_settings(MEDIA_ROOT=media_root):
                backup_media_directory(destination=archive_path)

            self.assertTrue(archive_path.exists())
            self.assertGreater(archive_path.stat().st_size, 0)

    def test_resolve_restore_artifacts_from_backup_dir(self):
        with workspace_temp_dir() as backup_root:
            backup_dir = backup_root / "snapshot"
            backup_dir.mkdir()
            (backup_dir / "database.sqlc").write_text("db", encoding="utf-8")
            (backup_dir / "media.tar.gz").write_text("media", encoding="utf-8")

            with override_settings(BACKUP_ROOT=backup_root):
                artifacts = resolve_restore_artifacts(backup_dir="snapshot")

            self.assertEqual(artifacts.db_dump_path, backup_dir / "database.sqlc")
            self.assertEqual(artifacts.media_archive_path, backup_dir / "media.tar.gz")
