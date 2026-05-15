from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError
from django.db import transaction

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.system.module_registry import MODULE_REGISTRY
from modules.v2.system.services import ensure_module_states, get_site_configuration


CRITICAL_PROD_ENV_NAMES = (
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "DB_ENGINE",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "CELERY_BROKER_URL",
)

ROLE_TO_FLAGS = {
    UserRole.USER: {"is_staff": False, "is_superuser": False},
    UserRole.FARMER: {"is_staff": False, "is_superuser": False},
    UserRole.MOD: {"is_staff": True, "is_superuser": False},
    UserRole.ADMIN: {"is_staff": True, "is_superuser": False},
    UserRole.CHIEF_ADMIN: {"is_staff": True, "is_superuser": False},
    UserRole.SUPER_ADMIN: {"is_staff": True, "is_superuser": True},
}


@dataclass(frozen=True)
class BootstrapResult:
    created_site_configuration: bool
    updated_site_configuration: bool
    created_module_state_keys: tuple[str, ...]
    updated_module_state_keys: tuple[str, ...]


@dataclass(frozen=True)
class PgConnectionSettings:
    host: str
    port: str
    dbname: str
    user: str
    password: str

    def as_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PGHOST"] = self.host
        env["PGPORT"] = self.port
        env["PGDATABASE"] = self.dbname
        env["PGUSER"] = self.user
        env["PGPASSWORD"] = self.password
        return env


@dataclass(frozen=True)
class BackupArtifacts:
    backup_dir: Path
    db_dump_path: Path | None
    media_archive_path: Path | None


@dataclass(frozen=True)
class RestoreArtifacts:
    db_dump_path: Path | None
    media_archive_path: Path | None


@dataclass(frozen=True)
class RoleGrantResult:
    username: str
    role: str
    is_staff: bool
    is_superuser: bool
    created_profile: bool


@dataclass(frozen=True)
class SmokeCheckResult:
    name: str
    ok: bool
    details: str



def _env_value(name: str) -> str:
    return (os.getenv(name, "") or "").strip()



def validate_critical_prod_env(required_names: Iterable[str] = CRITICAL_PROD_ENV_NAMES) -> None:
    missing: list[str] = []

    for name in required_names:
        value = _env_value(name)
        if not value:
            missing.append(name)

    if _env_value("DB_ENGINE").lower() != "postgres":
        raise CommandError("Production bootstrap requires DB_ENGINE=postgres.")

    if missing:
        raise CommandError(
            "Missing critical production environment variables: " + ", ".join(sorted(set(missing)))
        )



def sync_site_configuration_defaults(*, force_fill_blanks: bool = True) -> tuple[object, bool, bool]:
    from modules.v2.system.models import SiteConfiguration

    config_existed = SiteConfiguration.objects.filter(singleton_key=1).exists()
    config = get_site_configuration()
    defaults = config.bootstrap_defaults()
    created = not config_existed
    changed_fields: list[str] = []

    for field_name, default_value in defaults.items():
        current_value = getattr(config, field_name)
        if current_value in (None, "") and force_fill_blanks:
            setattr(config, field_name, default_value)
            changed_fields.append(field_name)

    if changed_fields:
        config.save(update_fields=[*changed_fields, "updated_at"])
        return config, created, True

    return config, created, False


@transaction.atomic
def bootstrap_system_state(*, sync_defaults: bool = True) -> BootstrapResult:
    from modules.v2.system.models import SiteConfiguration

    config_existed = SiteConfiguration.objects.filter(singleton_key=1).exists()
    get_site_configuration()
    config_created = not config_existed
    config_updated = False
    if sync_defaults:
        _config, _created, config_updated = sync_site_configuration_defaults(force_fill_blanks=True)
        config_created = config_created or _created

    existing_states = {row.key: row.is_enabled for row in settings_module_states()}
    ensure_module_states()
    actual_states = {row.key: row.is_enabled for row in settings_module_states()}

    created_keys = tuple(sorted(set(actual_states) - set(existing_states)))
    updated_keys = tuple(
        sorted(
            key for key, enabled in actual_states.items() if key in existing_states and existing_states[key] != enabled
        )
    )

    return BootstrapResult(
        created_site_configuration=config_created,
        updated_site_configuration=config_updated,
        created_module_state_keys=created_keys,
        updated_module_state_keys=updated_keys,
    )



def settings_module_states():
    from modules.v2.system.models import ModuleState

    return ModuleState.objects.order_by("key")


@transaction.atomic
def grant_role(*, username: str, role: str) -> RoleGrantResult:
    normalized_role = (role or "").strip()
    valid_roles = {choice for choice, _label in UserRole.choices}
    if normalized_role not in valid_roles:
        raise CommandError(f"Unknown role '{normalized_role}'. Allowed: {', '.join(sorted(valid_roles))}.")

    User = get_user_model()
    try:
        user = User.objects.select_for_update().get(username=username)
    except User.DoesNotExist as exc:
        raise CommandError(f"User '{username}' does not exist.") from exc

    profile, created_profile = UserProfile.objects.get_or_create(user=user)
    flags = ROLE_TO_FLAGS[normalized_role]

    user_changed_fields: list[str] = []
    if bool(user.is_staff) != flags["is_staff"]:
        user.is_staff = flags["is_staff"]
        user_changed_fields.append("is_staff")
    if bool(user.is_superuser) != flags["is_superuser"]:
        user.is_superuser = flags["is_superuser"]
        user_changed_fields.append("is_superuser")
    if user_changed_fields:
        user.save(update_fields=user_changed_fields)

    if profile.role != normalized_role:
        profile.role = normalized_role
        profile.save(update_fields=["role", "updated_at"])

    return RoleGrantResult(
        username=user.username,
        role=profile.role,
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
        created_profile=created_profile,
    )


@transaction.atomic
def create_or_update_superuser_from_env(*, username_env: str = "DJANGO_SUPERUSER_USERNAME", email_env: str = "DJANGO_SUPERUSER_EMAIL", password_env: str = "DJANGO_SUPERUSER_PASSWORD") -> RoleGrantResult:
    username = _env_value(username_env)
    email = _env_value(email_env)
    password = os.getenv(password_env, "")

    if not username:
        raise CommandError(f"{username_env} is required to create a superuser from environment.")
    if not password:
        raise CommandError(f"{password_env} is required to create a superuser from environment.")

    User = get_user_model()
    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_user(username=username, email=email)
    if email and getattr(user, "email", "") != email:
        user.email = email
    user.set_password(password)
    user.save()

    return grant_role(username=username, role=UserRole.SUPER_ADMIN)



def get_pg_connection_settings() -> PgConnectionSettings:
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise CommandError("backup/restore commands require PostgreSQL database backend.")

    return PgConnectionSettings(
        host=str(settings.DATABASES["default"]["HOST"]),
        port=str(settings.DATABASES["default"]["PORT"]),
        dbname=str(settings.DATABASES["default"]["NAME"]),
        user=str(settings.DATABASES["default"]["USER"]),
        password=str(settings.DATABASES["default"]["PASSWORD"]),
    )



def resolve_backup_root() -> Path:
    root = Path(getattr(settings, "BACKUP_ROOT", settings.BASE_DIR / "backups"))
    root.mkdir(parents=True, exist_ok=True)
    return root



def _ensure_command_available(name: str) -> None:
    if shutil.which(name) is None:
        raise CommandError(f"Required executable '{name}' is not available inside the current environment.")



def create_backup_paths(*, output_dir_name: str | None = None, include_db: bool = True, include_media: bool = True) -> BackupArtifacts:
    backup_root = resolve_backup_root()
    directory_name = output_dir_name or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / directory_name
    if backup_dir.exists():
        raise CommandError(f"Backup directory already exists: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=False)

    db_dump_path = backup_dir / "database.sqlc" if include_db else None
    media_archive_path = backup_dir / "media.tar.gz" if include_media else None
    return BackupArtifacts(backup_dir=backup_dir, db_dump_path=db_dump_path, media_archive_path=media_archive_path)



def backup_postgres_database(*, destination: Path) -> Path:
    _ensure_command_available("pg_dump")
    connection = get_pg_connection_settings()
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(destination),
    ]
    completed = subprocess.run(command, env=connection.as_env(), capture_output=True, text=True)
    if completed.returncode != 0:
        raise CommandError(completed.stderr.strip() or "pg_dump failed.")
    return destination



def backup_media_directory(*, destination: Path) -> Path:
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        raise CommandError(f"Media directory does not exist: {media_root}")

    with tarfile.open(destination, "w:gz") as archive:
        archive.add(media_root, arcname="media")
    return destination


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_media_archive_member(*, member: tarfile.TarInfo, extraction_root: Path, media_root: Path) -> None:
    member_name = str(member.name or "")
    member_path = Path(member_name)
    if not member_name or member_name.startswith("/") or member_path.is_absolute() or ".." in member_path.parts:
        raise CommandError(f"Unsafe media archive path: {member_name or '<empty>'}")

    if member.issym() or member.islnk():
        raise CommandError(f"Unsafe media archive link member: {member_name}")

    if not member.isdir() and not member.isfile():
        raise CommandError(f"Unsupported media archive member type: {member_name}")

    resolved_target = (extraction_root / member_path).resolve()
    resolved_media_root = media_root.resolve()
    if resolved_target != resolved_media_root and not _path_is_relative_to(resolved_target, resolved_media_root):
        raise CommandError(f"Unsafe media archive target: {member_name}")



def resolve_restore_artifacts(*, backup_dir: str | None = None, db_dump: str | None = None, media_archive: str | None = None) -> RestoreArtifacts:
    root = resolve_backup_root()
    base_dir = root / backup_dir if backup_dir else None

    resolved_db = Path(db_dump) if db_dump else (base_dir / "database.sqlc" if base_dir else None)
    resolved_media = Path(media_archive) if media_archive else (base_dir / "media.tar.gz" if base_dir else None)

    if resolved_db is not None and not resolved_db.exists():
        raise CommandError(f"Database backup file does not exist: {resolved_db}")
    if resolved_media is not None and not resolved_media.exists():
        raise CommandError(f"Media backup file does not exist: {resolved_media}")

    return RestoreArtifacts(db_dump_path=resolved_db, media_archive_path=resolved_media)



def restore_postgres_database(*, source: Path) -> None:
    _ensure_command_available("pg_restore")
    connection = get_pg_connection_settings()
    command = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        connection.dbname,
        str(source),
    ]
    completed = subprocess.run(command, env=connection.as_env(), capture_output=True, text=True)
    if completed.returncode != 0:
        raise CommandError(completed.stderr.strip() or "pg_restore failed.")



def restore_media_directory(*, source: Path) -> None:
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    extraction_root = media_root.parent

    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _validate_media_archive_member(
                member=member,
                extraction_root=extraction_root,
                media_root=media_root,
            )

    for child in media_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    with tarfile.open(source, "r:gz") as archive:
        archive.extractall(path=extraction_root, members=members)



def run_smoke_checks(*, base_url: str) -> list[SmokeCheckResult]:
    from urllib.error import URLError
    from urllib.request import urlopen

    from celery import current_app
    from django.core.management import call_command
    from io import StringIO

    results: list[SmokeCheckResult] = []

    check_stdout = StringIO()
    try:
        call_command("check", stdout=check_stdout, stderr=check_stdout)
        results.append(SmokeCheckResult(name="django_check", ok=True, details="System check passed."))
    except Exception as exc:  # pragma: no cover - exercised through command tests
        results.append(SmokeCheckResult(name="django_check", ok=False, details=str(exc)))

    try:
        health_url = base_url.rstrip("/") + "/health/"
        with urlopen(health_url, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace").strip()
            ok = response.status == 200 and body == "OK"
            results.append(
                SmokeCheckResult(
                    name="health_endpoint",
                    ok=ok,
                    details=f"{response.status} {body}".strip(),
                )
            )
    except URLError as exc:
        results.append(SmokeCheckResult(name="health_endpoint", ok=False, details=str(exc.reason)))

    config = get_site_configuration()
    results.append(
        SmokeCheckResult(
            name="site_configuration",
            ok=bool(config.site_name and config.site_currency_code),
            details=f"site_name={config.site_name}, public_base_url={config.public_base_url or '-'}",
        )
    )

    states = ensure_module_states()
    missing = sorted(set(MODULE_REGISTRY) - set(states))
    results.append(
        SmokeCheckResult(
            name="module_state_registry",
            ok=not missing,
            details="all module states present" if not missing else f"missing: {', '.join(missing)}",
        )
    )

    try:
        ping_result = current_app.control.inspect(timeout=3).ping() or {}
        ok = bool(ping_result)
        details = ", ".join(sorted(ping_result)) if ok else "no celery worker replies"
        results.append(SmokeCheckResult(name="celery_worker_ping", ok=ok, details=details))
    except Exception as exc:
        results.append(SmokeCheckResult(name="celery_worker_ping", ok=False, details=str(exc)))

    return results
