from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.system.services import get_site_configuration

from .models import AdminAuditLog, AuditAction, UserBan
from .services.users import lift_expired_bans

User = get_user_model()
PASSWORD = "StrongPass123"


def make_user(username: str, role: str = UserRole.USER):
    user = User.objects.create_user(username=username, password=PASSWORD)
    profile = UserProfile.objects.get(user=user)
    profile.role = role
    profile.save(update_fields=["role"])
    return user


class ControlCenterAccessTests(TestCase):
    def test_pages_forbidden_for_regular_user(self):
        make_user("plain")
        self.client.login(username="plain", password=PASSWORD)
        for name in ("control_center:users", "control_center:audit", "control_center:ledger", "control_center:moderation", "control_center:health"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_pages_render_for_admin(self):
        make_user("boss", UserRole.ADMIN)
        target = make_user("someone")
        self.client.login(username="boss", password=PASSWORD)
        for url in (
            reverse("frontend:admin_panel"),
            reverse("control_center:users"),
            reverse("control_center:user_detail", args=[target.pk]),
            reverse("control_center:audit"),
            reverse("control_center:ledger"),
            reverse("control_center:moderation"),
            reverse("control_center:health"),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, "cc-tabs")


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", UserRole.ADMIN)
        self.target = make_user("victim")
        self.client.login(username="admin", password=PASSWORD)

    def _action(self, user, **data):
        return self.client.post(reverse("control_center:user_action", args=[user.pk]), data)

    def test_ban_blocks_login_and_writes_audit(self):
        response = self._action(self.target, action="ban", reason="Scam", duration="7d")
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        ban = UserBan.objects.get(user=self.target)
        self.assertTrue(ban.is_active)
        self.assertIsNotNone(ban.expires_at)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.USER_BAN, target_user=self.target, actor=self.admin).exists())

        self.client.logout()
        self.assertFalse(self.client.login(username="victim", password=PASSWORD))

    def test_ban_requires_reason(self):
        self._action(self.target, action="ban", reason=" ", duration="1d")
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertFalse(UserBan.objects.exists())

    def test_unban_restores_account(self):
        self._action(self.target, action="ban", reason="Spam", duration="forever")
        self._action(self.target, action="unban")
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertFalse(UserBan.objects.active().filter(user=self.target).exists())
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.USER_UNBAN).exists())

    def test_ban_ends_existing_sessions(self):
        other = self.client_class()
        self.assertTrue(other.login(username="victim", password=PASSWORD))
        self._action(self.target, action="ban", reason="Abuse", duration="1h")
        response = other.get(reverse("profiles:profile"))
        self.assertEqual(response.status_code, 302)

    def test_expired_ban_is_lifted(self):
        self._action(self.target, action="ban", reason="Cooldown", duration="1h")
        UserBan.objects.filter(user=self.target).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(lift_expired_bans(), 1)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)

    def test_admin_cannot_ban_equal_rank_or_self(self):
        peer = make_user("peer", UserRole.ADMIN)
        self._action(peer, action="ban", reason="x", duration="1d")
        self._action(self.admin, action="ban", reason="x", duration="1d")
        self.assertFalse(UserBan.objects.exists())

    def test_role_change_limited_below_own_rank(self):
        self._action(self.target, action="role", role=UserRole.MOD)
        self.assertEqual(UserProfile.objects.get(user=self.target).role, UserRole.MOD)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.USER_ROLE).exists())

        self._action(self.target, action="role", role=UserRole.ADMIN)
        self.assertEqual(UserProfile.objects.get(user=self.target).role, UserRole.MOD)

    def test_note_goes_to_audit(self):
        self._action(self.target, action="note", note="Проверить трейды")
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.USER_NOTE, summary="Проверить трейды").exists())

    def test_users_list_filters_banned(self):
        self._action(self.target, action="ban", reason="Scam", duration="1d")
        make_user("innocent")
        response = self.client.get(reverse("control_center:users"), {"status": "banned"})
        self.assertContains(response, "victim")
        self.assertNotContains(response, "innocent")

    def test_users_export_csv(self):
        response = self.client.get(reverse("control_center:users_export"), {"q": "victim"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertIn("victim", body)
        self.assertNotIn(";admin;", body)


class SiteAndModerationTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", UserRole.ADMIN)
        self.client.login(username="admin", password=PASSWORD)

    def test_maintenance_toggle(self):
        self.client.post(reverse("control_center:site_toggle"), {"field": "maintenance", "enabled": "1"})
        self.assertTrue(get_site_configuration().maintenance_mode_enabled)
        self.client.post(reverse("control_center:site_toggle"), {"field": "maintenance", "enabled": "0"})
        self.assertFalse(get_site_configuration().maintenance_mode_enabled)
        self.assertEqual(AdminAuditLog.objects.filter(action=AuditAction.SITE_MAINTENANCE).count(), 2)

    def test_site_toggle_forbidden_for_regular_user(self):
        make_user("plain")
        self.client.login(username="plain", password=PASSWORD)
        response = self.client.post(reverse("control_center:site_toggle"), {"field": "registration", "enabled": "0"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(get_site_configuration().registration_enabled)

    def test_bulk_approve_and_disable(self):
        first = SteamItem.objects.create(market_hash_name="AK-47 | Redline (Field-Tested)", needs_review=True)
        second = SteamItem.objects.create(market_hash_name="AWP | Asiimov (Field-Tested)", needs_review=True)
        self.client.post(reverse("control_center:moderation_action"), {"action": "approve", "items": [first.pk]})
        self.client.post(reverse("control_center:moderation_action"), {"action": "disable", "items": [second.pk]})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.needs_review)
        self.assertTrue(first.is_active)
        self.assertFalse(second.is_active)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.CATALOG_APPROVE).exists())

    def test_ledger_filters_and_export(self):
        user = make_user("payer")
        LedgerEntry.objects.create(user=user, delta=Decimal("50.00"), balance_after=Decimal("50.00"), reason=LedgerReason.TOPUP)
        LedgerEntry.objects.create(user=user, delta=Decimal("-20.00"), balance_after=Decimal("30.00"), reason=LedgerReason.CASE_OPEN)
        response = self.client.get(reverse("control_center:ledger"), {"direction": "out"})
        self.assertEqual(response.context["totals"]["count"], 1)
        self.assertEqual(response.context["totals"]["spent"], Decimal("-20.00"))
        export = self.client.get(reverse("control_center:ledger_export"), {"reason": "topup"})
        self.assertIn("topup", export.content.decode("utf-8"))
        self.assertNotIn("case_open", export.content.decode("utf-8"))


class HealthFormattingTests(TestCase):
    def test_format_ms_keeps_sub_millisecond_values(self):
        from .services.health import format_ms

        self.assertEqual(format_ms(0.04), "<0.1 мс")
        self.assertEqual(format_ms(0.42), "0.4 мс")
        self.assertEqual(format_ms(37.6), "38 мс")

    def test_disk_check_shows_fractional_percent_and_total(self):
        from collections import namedtuple
        from unittest import mock

        from .services import health

        usage = namedtuple("usage", "total used free")(1000 * 1024**3, 4 * 1024**3, 996 * 1024**3)
        with mock.patch.object(health.shutil, "disk_usage", return_value=usage):
            check = health.check_disk()
        self.assertEqual(check.value, "996.0 ГБ свободно")
        self.assertEqual(check.hint, "Занято 0.4% из 1000.0 ГБ")

    def test_database_check_reports_size(self):
        from .services.health import check_database

        self.assertIn("sqlite", check_database().hint)


class SystemSettingsToolsTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.admin = make_user("admin", UserRole.ADMIN)
        self.client.login(username="admin", password=PASSWORD)

    def _form_data(self, **overrides):
        form = self.client.get(reverse("system_config:settings")).context["form"]
        data = {}
        for name in form.fields:
            value = form[name].value()
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif value is not None:
                data[name] = value
        data.update(overrides)
        return {key: value for key, value in data.items() if value is not False}

    def test_settings_page_renders_in_control_center(self):
        response = self.client.get(reverse("system_config:settings"))
        self.assertContains(response, "cc-tabs")
        self.assertContains(response, "Настройки системы")
        self.assertContains(response, reverse("system_config:check_integrations"))

    def test_settings_page_does_not_call_external_apis(self):
        from unittest import mock

        with mock.patch("modules.v2.system.settings_tools.get_external_service_health") as live:
            self.client.get(reverse("system_config:settings"))
        live.assert_not_called()

    def test_saving_settings_writes_diff_to_audit_without_secrets(self):
        response = self.client.post(
            reverse("system_config:settings"),
            self._form_data(site_name="CaseUP Pro", steam_api_key="super-secret-key"),
        )
        self.assertEqual(response.status_code, 302)
        entry = AdminAuditLog.objects.get(action=AuditAction.SETTINGS_UPDATE)
        fields = {change["field"]: change for change in entry.payload["changes"]}
        self.assertEqual(fields["config.site_name"]["to"], "CaseUP Pro")
        self.assertEqual(fields["integration.steam_api_key"]["to"], "••••")
        self.assertNotIn("super-secret-key", str(entry.payload))
        self.assertContains(self.client.get(reverse("system_config:settings")), "CaseUP Pro")

    def test_saving_without_changes_is_not_logged(self):
        self.client.post(reverse("system_config:settings"), self._form_data())
        self.assertFalse(AdminAuditLog.objects.filter(action=AuditAction.SETTINGS_UPDATE).exists())

    def test_check_integrations_caches_result(self):
        from unittest import mock

        from modules.v2.system.services import ExternalServiceHealth

        fake = [ExternalServiceHealth(key="steam", label="Steam API", status="up", mode="auto", has_key=True, message="ok")]
        with mock.patch("modules.v2.system.settings_tools.get_external_service_health", return_value=fake) as live:
            response = self.client.post(reverse("system_config:check_integrations"))
            self.client.get(reverse("system_config:settings"))
        self.assertEqual(response.status_code, 302)
        live.assert_called_once()
        page = self.client.get(reverse("system_config:settings"))
        self.assertIsNotNone(page.context["integration_checked_at"])
        self.assertEqual(page.context["integration_health"][0].status, "up")

    def test_clear_cache_is_audited(self):
        from django.core.cache import cache

        cache.set("probe", 1)
        self.client.post(reverse("system_config:clear_cache"))
        self.assertIsNone(cache.get("probe"))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.CACHE_CLEAR).exists())

    def test_export_has_no_secrets(self):
        self.client.post(reverse("system_config:settings"), self._form_data(telegram_bot_token="123:secret-token"))
        response = self.client.get(reverse("system_config:export_settings"))
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        self.assertNotIn("secret-token", body)
        self.assertTrue(response.json()["integrations"]["telegram_bot_token_set"])

    def test_maintenance_preview_uses_draft_message(self):
        response = self.client.get(reverse("system_config:maintenance_preview"), {"message": "Скоро вернёмся"})
        self.assertContains(response, "Скоро вернёмся")
        self.assertContains(response, "Предпросмотр")

    def test_tools_forbidden_for_regular_user(self):
        make_user("plain")
        self.client.login(username="plain", password=PASSWORD)
        for name in ("check_integrations", "clear_cache"):
            self.assertEqual(self.client.post(reverse(f"system_config:{name}")).status_code, 403, name)
        for name in ("export_settings", "maintenance_preview"):
            self.assertEqual(self.client.get(reverse(f"system_config:{name}")).status_code, 403, name)
