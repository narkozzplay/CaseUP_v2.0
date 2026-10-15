"""Two-factor authentication with an authenticator app (TOTP, RFC 6238) plus one-time backup codes.

Flow: a login (Steam or password) creates the session as usual, but when the account has 2FA the
session is marked "pending" (PENDING_KEY) and TwoFactorMiddleware lets it reach nothing except the
code page and logout until the code is entered. Admin+ accounts must have 2FA at all: without it the
middleware keeps them on the Безопасность page until it is set up.

Wrong codes are counted per user (MAX_FAILURES per FAILURE_WINDOW, in the cache), the same counter
for the login step and for the code checks on the security page.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import TwoFactorAuth

PENDING_KEY = "two_factor_pending"
NEW_BACKUP_CODES_KEY = "two_factor_new_backup_codes"

MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)
BACKUP_CODE_COUNT = 10
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
_STEP_SECONDS = 30
_FAILURES_PREFIX = "two-factor-failures:"

METHOD_TOTP = "totp"
METHOD_BACKUP = "backup"


class TwoFactorLocked(Exception):
    """Too many wrong codes recently; the user has to wait."""


# --------------------------------------------------------------------------- state


def get_record(user) -> TwoFactorAuth | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return TwoFactorAuth.objects.filter(user=user).first()


def is_enabled(user) -> bool:
    return bool(getattr(user, "is_authenticated", False)) and TwoFactorAuth.objects.filter(user=user, confirmed_at__isnull=False).exists()


def is_required(user) -> bool:
    """Admin+ (and superusers) must protect their account with 2FA."""
    if not getattr(settings, "TWO_FACTOR_REQUIRED_FOR_ADMINS", True):
        return False
    from modules.v2.profiles.rbac import get_user_role

    return get_user_role(user).is_admin_plus


# --------------------------------------------------------------------------- codes


def _normalize(code: str) -> str:
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def _hash_backup(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def generate_backup_codes() -> list[str]:
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def _totp(secret: str):
    import pyotp

    return pyotp.TOTP(secret)


def _matching_step(secret: str, code: str, *, now=None) -> int | None:
    """Time step the code belongs to (current one ±1 for clock drift), or None."""
    digits = _normalize(code)
    if len(digits) != 6 or not digits.isdigit():
        return None
    totp = _totp(secret)
    now = now or timezone.now()
    for drift in (0, -1, 1):
        moment = now + timedelta(seconds=drift * _STEP_SECONDS)
        if hmac.compare_digest(totp.at(moment), digits):
            return totp.timecode(moment)
    return None


def provisioning_uri(record: TwoFactorAuth, user) -> str:
    from modules.v2.system.services import get_site_configuration

    issuer = (getattr(get_site_configuration(), "site_name", "") or "CaseUP").strip() or "CaseUP"
    return _totp(record.secret).provisioning_uri(name=user.get_username(), issuer_name=issuer)


def qr_data_uri(uri: str) -> str:
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#111", light="#fff")


def format_secret(secret: str) -> str:
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


# --------------------------------------------------------------------------- rate limit


def _failures_key(user) -> str:
    return f"{_FAILURES_PREFIX}{user.pk}"


def is_locked(user) -> bool:
    return int(cache.get(_failures_key(user)) or 0) >= MAX_FAILURES


def _register_failure(user) -> int:
    key = _failures_key(user)
    timeout = int(FAILURE_WINDOW.total_seconds())
    if cache.add(key, 1, timeout):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout)
        return 1


def reset_failures(user) -> None:
    cache.delete(_failures_key(user))


# --------------------------------------------------------------------------- verification


def verify(user, code: str, *, allow_backup: bool = True, request=None, context: str = "login") -> str | None:
    """Check a code for an enabled 2FA. Returns METHOD_TOTP / METHOD_BACKUP, or None for a wrong code.

    Raises TwoFactorLocked after too many wrong codes. A TOTP code is accepted once (replay-safe);
    a backup code is used up.
    """
    if is_locked(user):
        raise TwoFactorLocked
    method = None
    with transaction.atomic():
        record = TwoFactorAuth.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
        if record is None:
            return None
        step = _matching_step(record.secret, code)
        if step is not None and step > record.last_used_step:
            record.last_used_step = step
            method = METHOD_TOTP
        elif allow_backup and step is None:
            hashed = _hash_backup(code)
            remaining = [value for value in record.backup_code_hashes or [] if not hmac.compare_digest(value, hashed)]
            if len(remaining) < len(record.backup_code_hashes or []):
                record.backup_code_hashes = remaining
                method = METHOD_BACKUP
        if method:
            record.last_used_at = timezone.now()
            record.save(update_fields=["last_used_step", "backup_code_hashes", "last_used_at"])

    if method:
        reset_failures(user)
        if method == METHOD_BACKUP:
            _log(user, "account.2fa_backup_used", f"Вход по резервному коду, осталось: {record.backup_codes_left}", request)
        return method

    failures = _register_failure(user)
    _log(user, "account.2fa_failed", f"Неверный код 2FA ({context}), попытка {failures} из {MAX_FAILURES}", request)
    if failures >= MAX_FAILURES:
        _notify_locked(user)
    return None


# --------------------------------------------------------------------------- setup / disable


def start_setup(user) -> TwoFactorAuth:
    """Create (or restart) a setup with a fresh secret. An enabled 2FA is left untouched."""
    import pyotp

    record = get_record(user)
    if record is not None and record.is_enabled:
        return record
    if record is None:
        record = TwoFactorAuth(user=user)
    record.secret = pyotp.random_base32()
    record.created_at = timezone.now()
    record.last_used_step = 0
    record.backup_code_hashes = []
    record.save()
    return record


def cancel_setup(user) -> None:
    TwoFactorAuth.objects.filter(user=user, confirmed_at__isnull=True).delete()


def confirm_setup(user, code: str, *, request=None) -> list[str] | None:
    """Finish the setup with the first code from the app. Returns the backup codes (shown once)."""
    if is_locked(user):
        raise TwoFactorLocked
    record = TwoFactorAuth.objects.filter(user=user, confirmed_at__isnull=True).first()
    if record is None:
        return None
    step = _matching_step(record.secret, code)
    if step is None:
        failures = _register_failure(user)
        _log(user, "account.2fa_failed", f"Неверный код 2FA (подключение), попытка {failures} из {MAX_FAILURES}", request)
        return None
    codes = generate_backup_codes()
    now = timezone.now()
    record.confirmed_at = now
    record.last_used_at = now
    record.last_used_step = step
    record.backup_code_hashes = [_hash_backup(value) for value in codes]
    record.save(update_fields=["confirmed_at", "last_used_at", "last_used_step", "backup_code_hashes"])
    reset_failures(user)
    _log(user, "account.2fa_enabled", "Двухфакторная защита включена", request)
    return codes


def regenerate_backup_codes(user, *, request=None) -> list[str]:
    codes = generate_backup_codes()
    TwoFactorAuth.objects.filter(user=user, confirmed_at__isnull=False).update(backup_code_hashes=[_hash_backup(value) for value in codes])
    _log(user, "account.2fa_backup_regenerated", "Созданы новые резервные коды 2FA", request)
    return codes


def disable(user, *, request=None, summary: str = "Двухфакторная защита отключена") -> bool:
    deleted, _ = TwoFactorAuth.objects.filter(user=user).delete()
    reset_failures(user)
    if deleted:
        _log(user, "account.2fa_disabled", summary, request)
    return bool(deleted)


# --------------------------------------------------------------------------- login step


def mark_pending_if_enabled(request, user) -> bool:
    """Called on user_logged_in: the new session must pass the code step before it is usable."""
    if not is_enabled(user):
        return False
    request.session[PENDING_KEY] = True
    return True


def is_pending(request) -> bool:
    session = getattr(request, "session", None)
    return bool(session is not None and session.get(PENDING_KEY))


def complete_pending(request) -> None:
    request.session.pop(PENDING_KEY, None)


# --------------------------------------------------------------------------- side effects


def _log(user, event: str, summary: str, request=None) -> None:
    from modules.v2.control_center.models import ActivityCategory
    from modules.v2.control_center.services.activity import log_activity

    log_activity(category=ActivityCategory.SECURITY, event=event, summary=summary, user=user, request=request)


def _notify_locked(user) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_TWO_FACTOR_LOCKED

    try:
        notify(user, KIND_TWO_FACTOR_LOCKED, dedupe=False, payload={"minutes": int(FAILURE_WINDOW.total_seconds() // 60)})
    except Exception:  # noqa: BLE001 - a notification must never break the check
        import logging

        logging.getLogger(__name__).exception("2FA lockout notification failed for user %s", user.pk)
