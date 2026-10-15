from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pyotp
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import ActivityLog, AdminAuditLog, AuditAction
from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile, UserRole

from . import two_factor
from .models import TwoFactorAuth
from .notifications import KIND_TWO_FACTOR_LOCKED

User = get_user_model()
PASSWORD = "StrongPass123"
BACKUP_CODES = ["aaaa-bbbb", "cccc-dddd", "eeee-ffff"]


def enable_two_factor(user) -> str:
    secret = pyotp.random_base32()
    TwoFactorAuth.objects.create(
        user=user,
        secret=secret,
        confirmed_at=timezone.now(),
        backup_code_hashes=[two_factor._hash_backup(code) for code in BACKUP_CODES],
    )
    return secret


def previous_code(secret: str) -> str:
    """A valid code of the previous 30-second step (accepted for clock drift, distinct from now())."""
    return pyotp.TOTP(secret).at(timezone.now() - timedelta(seconds=30))


def make_role(user, role):
    profile = UserProfile.objects.get(user=user)
    profile.role = role
    profile.save(update_fields=["role"])


class TwoFactorSetupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="tfa_user", password=PASSWORD)
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("accounts:security")

    def test_enable_flow_shows_qr_then_backup_codes_once(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Включить двухфакторную защиту")

        self.client.post(self.url, {"action": "2fa_start"})
        record = TwoFactorAuth.objects.get(user=self.user)
        self.assertFalse(record.is_enabled)
        response = self.client.get(self.url)
        self.assertContains(response, "data:image/svg+xml")
        self.assertContains(response, two_factor.format_secret(record.secret))

        self.client.post(self.url, {"action": "2fa_confirm", "code": "000000"})
        self.assertFalse(TwoFactorAuth.objects.get(user=self.user).is_enabled)

        self.client.post(self.url, {"action": "2fa_confirm", "code": pyotp.TOTP(record.secret).now()})
        record.refresh_from_db()
        self.assertTrue(record.is_enabled)
        self.assertEqual(record.backup_codes_left, two_factor.BACKUP_CODE_COUNT)

        first = self.client.get(self.url)
        self.assertEqual(len(first.context["new_backup_codes"]), two_factor.BACKUP_CODE_COUNT)
        self.assertIsNone(self.client.get(self.url).context["new_backup_codes"])
        self.assertTrue(ActivityLog.objects.filter(user=self.user, event="account.2fa_enabled").exists())

    def test_secret_is_stored_encrypted(self):
        secret = enable_two_factor(self.user)
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT secret FROM accounts_v2_twofactorauth WHERE user_id = %s", [self.user.pk])
            raw = cursor.fetchone()[0]
        self.assertNotIn(secret, raw)

    def test_code_cannot_be_reused(self):
        secret = enable_two_factor(self.user)
        code = pyotp.TOTP(secret).now()
        self.assertEqual(two_factor.verify(self.user, code), two_factor.METHOD_TOTP)
        self.assertIsNone(two_factor.verify(self.user, code))

    def test_disable_needs_a_code(self):
        secret = enable_two_factor(self.user)
        self.client.post(self.url, {"action": "2fa_disable", "code": "123"})
        self.assertTrue(two_factor.is_enabled(self.user))
        self.client.post(self.url, {"action": "2fa_disable", "code": pyotp.TOTP(secret).now()})
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_regenerate_backup_codes_replaces_old_ones(self):
        secret = enable_two_factor(self.user)
        self.client.post(self.url, {"action": "2fa_backup", "code": BACKUP_CODES[0]})  # backup codes don't count here
        self.assertEqual(TwoFactorAuth.objects.get(user=self.user).backup_codes_left, len(BACKUP_CODES))

        self.client.post(self.url, {"action": "2fa_backup", "code": pyotp.TOTP(secret).now()})
        self.assertEqual(TwoFactorAuth.objects.get(user=self.user).backup_codes_left, two_factor.BACKUP_CODE_COUNT)
        self.assertIsNone(two_factor.verify(self.user, BACKUP_CODES[1]))


class TwoFactorLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="tfa_login", password=PASSWORD, is_staff=True)
        self.secret = enable_two_factor(self.user)
        self.verify_url = reverse("accounts:two_factor_verify")

    def _login(self):
        client = Client()
        response = client.post(reverse("admin:login"), {"username": "tfa_login", "password": PASSWORD, "next": "/"})
        self.assertEqual(response.status_code, 302)
        return client

    def test_session_is_locked_until_the_code_is_entered(self):
        client = self._login()
        response = client.get(reverse("accounts:security"))
        self.assertRedirects(response, f"{self.verify_url}?next=%2Fauth%2Fsecurity%2F", fetch_redirect_response=False)
        self.assertEqual(client.get(self.verify_url).status_code, 200)

        response = client.post(self.verify_url, {"code": "000000", "next": "/auth/security/"})
        self.assertContains(response, "Неверный код")
        self.assertEqual(client.get(reverse("accounts:security")).status_code, 302)

        response = client.post(self.verify_url, {"code": previous_code(self.secret), "next": "/auth/security/"})
        self.assertRedirects(response, "/auth/security/", fetch_redirect_response=False)
        self.assertEqual(client.get(reverse("accounts:security")).status_code, 200)

    def test_backup_code_works_once(self):
        client = self._login()
        response = client.post(self.verify_url, {"code": BACKUP_CODES[0].upper().replace("-", " ")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TwoFactorAuth.objects.get(user=self.user).backup_codes_left, len(BACKUP_CODES) - 1)
        self.assertTrue(ActivityLog.objects.filter(user=self.user, event="account.2fa_backup_used").exists())

        other = self._login()
        self.assertContains(other.post(self.verify_url, {"code": BACKUP_CODES[0]}), "Неверный код")

    def test_lockout_after_too_many_wrong_codes(self):
        client = self._login()
        for _ in range(two_factor.MAX_FAILURES):
            client.post(self.verify_url, {"code": "000000"})
        response = client.post(self.verify_url, {"code": previous_code(self.secret)})
        self.assertContains(response, "Слишком много неверных кодов")
        self.assertEqual(client.get(reverse("accounts:security")).status_code, 302)
        self.assertTrue(Notification.objects.filter(recipient=self.user, kind=KIND_TWO_FACTOR_LOCKED).exists())
        self.assertEqual(ActivityLog.objects.filter(user=self.user, event="account.2fa_failed").count(), two_factor.MAX_FAILURES)

    def test_logout_is_allowed_while_pending(self):
        client = self._login()
        client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", client.session)

    def test_account_without_two_factor_logs_in_directly(self):
        TwoFactorAuth.objects.all().delete()
        client = self._login()
        self.assertEqual(client.get(reverse("accounts:security")).status_code, 200)


@override_settings(TWO_FACTOR_REQUIRED_FOR_ADMINS=True)
class TwoFactorRequiredForAdminsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="tfa_admin", password=PASSWORD)
        make_role(self.admin, UserRole.ADMIN)
        self.client = Client()
        self.client.force_login(self.admin)

    def test_admin_without_two_factor_is_sent_to_setup(self):
        response = self.client.get(reverse("control_center:users"))
        self.assertRedirects(response, f"{reverse('accounts:security')}?required=1", fetch_redirect_response=False)
        page = self.client.get(reverse("accounts:security"))
        self.assertContains(page, "обязательна")

        enable_two_factor(self.admin)
        self.assertEqual(self.client.get(reverse("control_center:users")).status_code, 200)

    def test_admin_cannot_disable(self):
        secret = enable_two_factor(self.admin)
        self.client.post(reverse("accounts:security"), {"action": "2fa_disable", "code": pyotp.TOTP(secret).now()})
        self.assertTrue(two_factor.is_enabled(self.admin))

    def test_regular_user_is_not_forced(self):
        user = User.objects.create_user(username="tfa_regular", password=PASSWORD)
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get(reverse("profiles:profile")).status_code, 200)


class TwoFactorResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="tfa_lost", password=PASSWORD)
        enable_two_factor(self.user)

    def test_admin_resets_from_control_center(self):
        admin = User.objects.create_user(username="tfa_chief", password=PASSWORD)
        make_role(admin, UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        self.assertContains(client.get(reverse("control_center:user_detail", args=[self.user.pk])), "Сбросить 2FA")

        client.post(reverse("control_center:user_action", args=[self.user.pk]), {"action": "reset_2fa"})
        self.assertFalse(two_factor.is_enabled(self.user))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.USER_2FA_RESET, target_user=self.user, actor=admin).exists())

    def test_admin_cannot_reset_a_higher_role(self):
        make_role(self.user, UserRole.CHIEF_ADMIN)
        admin = User.objects.create_user(username="tfa_low_admin", password=PASSWORD)
        make_role(admin, UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        client.post(reverse("control_center:user_action", args=[self.user.pk]), {"action": "reset_2fa"})
        self.assertTrue(two_factor.is_enabled(self.user))

    def test_management_command(self):
        out = StringIO()
        call_command("reset_2fa", "tfa_lost", stdout=out)
        self.assertIn("2FA is off", out.getvalue())
        self.assertFalse(two_factor.is_enabled(self.user))
