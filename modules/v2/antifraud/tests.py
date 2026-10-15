from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import AdminAuditLog, AuditAction, UserBan
from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.referrals.models import Referral

from . import services
from .models import IpBan, MultiAccountCase, UserIp
from .notifications import KIND_MULTI_ACCOUNT

User = get_user_model()
PASSWORD = "StrongPass123"


def make_user(username: str, role: str = UserRole.USER) -> User:
    user = User.objects.create_user(username=username, password=PASSWORD)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if role != UserRole.USER:
        profile.role = role
        profile.save(update_fields=["role"])
    return User.objects.get(pk=user.pk)  # fresh instance: no stale cached profile/role


class AntifraudTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("af_admin", UserRole.ADMIN)

    def seen(self, user, ip, *, days_ago: int = 0):
        services.record_ip(user.pk, ip, now=timezone.now() - timedelta(days=days_ago))


class RecordingTests(AntifraudTestCase):
    def test_login_records_public_ip_only(self):
        user = make_user("af_login")
        user.is_staff = True
        user.save()
        Client(REMOTE_ADDR="95.24.10.20").post(reverse("admin:login"), {"username": "af_login", "password": PASSWORD})
        Client(REMOTE_ADDR="192.168.1.5").post(reverse("admin:login"), {"username": "af_login", "password": PASSWORD})
        self.assertEqual(list(UserIp.objects.filter(user=user).values_list("ip", flat=True)), ["95.24.10.20"])

    def test_repeated_ip_updates_the_row(self):
        user = make_user("af_repeat")
        self.seen(user, "95.24.10.21", days_ago=3)
        self.seen(user, "95.24.10.21")
        row = UserIp.objects.get(user=user)
        self.assertEqual(row.hits, 2)
        self.assertGreater(row.last_seen, row.first_seen)


class DetectionTests(AntifraudTestCase):
    def test_group_is_found_and_admins_are_notified(self):
        a, b, c = make_user("af_a"), make_user("af_b"), make_user("af_c")
        self.seen(a, "95.24.1.1")
        self.seen(b, "95.24.1.1")
        self.seen(b, "95.24.1.2")
        self.seen(c, "95.24.1.2")  # c shares a different IP with b: one group of three
        make_user("af_alone")
        result = services.detect()
        self.assertEqual((result.groups, result.new_cases), (1, 1))
        case = MultiAccountCase.objects.get()
        self.assertEqual(set(case.members.values_list("username", flat=True)), {"af_a", "af_b", "af_c"})
        self.assertEqual(case.shared_ips, ["95.24.1.1", "95.24.1.2"])
        self.assertTrue(Notification.objects.filter(recipient=self.admin, kind=KIND_MULTI_ACCOUNT).exists())

        services.detect()  # nothing new: no second case, no second alert
        self.assertEqual(MultiAccountCase.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.admin, kind=KIND_MULTI_ACCOUNT).count(), 1)

    def test_old_activity_is_ignored(self):
        a, b = make_user("af_old1"), make_user("af_old2")
        self.seen(a, "95.24.2.1", days_ago=40)
        self.seen(b, "95.24.2.1")
        self.assertEqual(services.detect().groups, 0)

    @override_settings(MULTI_ACCOUNT_SHARED_IP_LIMIT=3)
    def test_busy_ip_is_a_shared_network(self):
        users = [make_user(f"af_nat{i}") for i in range(5)]
        for user in users:
            self.seen(user, "95.24.3.3")
        result = services.detect()
        self.assertEqual((result.groups, result.shared_networks), (0, 1))

    def test_clean_group_alerts_again_only_for_a_new_account(self):
        a, b = make_user("af_fam1"), make_user("af_fam2")
        self.seen(a, "95.24.4.4")
        self.seen(b, "95.24.4.4")
        services.detect()
        case = MultiAccountCase.objects.get()
        services.mark_clean(actor=self.admin, case=case, note="братья")
        services.detect()
        self.assertEqual(MultiAccountCase.objects.get().status, MultiAccountCase.Status.CLEAN)

        newcomer = make_user("af_fam3")
        self.seen(newcomer, "95.24.4.4")
        result = services.detect()
        case.refresh_from_db()
        self.assertEqual((result.reopened, case.status), (1, MultiAccountCase.Status.OPEN))
        self.assertTrue(case.memberships.get(user=newcomer).is_new)
        self.assertEqual(Notification.objects.filter(recipient=self.admin, kind=KIND_MULTI_ACCOUNT).count(), 2)

    def test_two_known_groups_are_merged(self):
        a, b, c, d = (make_user(f"af_m{i}") for i in range(4))
        self.seen(a, "95.24.5.1")
        self.seen(b, "95.24.5.1")
        self.seen(c, "95.24.5.2")
        self.seen(d, "95.24.5.2")
        services.detect()
        self.assertEqual(MultiAccountCase.objects.count(), 2)
        self.seen(b, "95.24.5.2")  # b now links both groups
        services.detect()
        self.assertEqual(MultiAccountCase.objects.count(), 1)
        self.assertEqual(MultiAccountCase.objects.get().members.count(), 4)


class DecisionTests(AntifraudTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = make_user("af_abuser1"), make_user("af_abuser2")
        self.seen(self.a, "95.24.6.6")
        self.seen(self.b, "95.24.6.6")
        services.detect()
        self.case = MultiAccountCase.objects.get()
        Referral.objects.create(referrer=self.a, referee=self.b, qualified_at=timezone.now())
        self.client = Client()
        self.client.force_login(self.admin)

    def test_abuse_bans_accounts_ips_and_internal_referrals(self):
        response = self.client.post(
            reverse("control_center:multi_account_decide", args=[self.case.pk]),
            {"decision": "abuse", "ban_user": [self.a.pk, self.b.pk], "duration": "30d", "ban_ips": "1", "ip_duration": "7d", "note": "фарм промокодов"},
        )
        self.assertRedirects(response, reverse("control_center:multi_account_detail", args=[self.case.pk]), fetch_redirect_response=False)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, MultiAccountCase.Status.ABUSE)
        self.assertEqual(UserBan.objects.active().filter(user__in=[self.a, self.b]).count(), 2)
        self.assertTrue(IpBan.objects.active().filter(network="95.24.6.6/32", case=self.case).exists())
        self.assertEqual(Referral.objects.get(referee=self.b).review, Referral.Review.BLOCKED)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.MULTI_ACCOUNT_DECISION, actor=self.admin).exists())
        self.assertIn("фарм промокодов", self.case.note)

    def test_admin_cannot_ban_a_higher_role_member(self):
        chief = make_user("af_chief", UserRole.CHIEF_ADMIN)
        self.seen(chief, "95.24.6.6")
        services.detect()
        result = services.mark_abuse(actor=self.admin, case=self.case, user_ids=[self.a.pk, chief.pk], duration="1d", ban_ips=False, ip_duration="1d")
        self.assertEqual(result["skipped"], ["af_chief"])
        self.assertFalse(UserBan.objects.active().filter(user=chief).exists())

    def test_pages_render(self):
        self.assertContains(self.client.get(reverse("control_center:multi_accounts")), "af_abuser1")
        page = self.client.get(reverse("control_center:multi_account_detail", args=[self.case.pk]))
        self.assertContains(page, "95.24.6.6")
        self.assertContains(page, "Рефералы внутри группы")
        user_page = self.client.get(reverse("control_center:user_detail", args=[self.a.pk]))
        self.assertContains(user_page, f"мульти-аккаунт #{self.case.pk}")

    def test_regular_user_has_no_access(self):
        client = Client()
        client.force_login(self.a)
        self.assertEqual(client.get(reverse("control_center:multi_accounts")).status_code, 403)


class IpBanTests(AntifraudTestCase):
    def test_banned_range_is_blocked_but_admins_pass(self):
        services.create_ip_ban(actor=self.admin, network="95.24.7.0/24", reason="абуз", duration="1d")
        blocked = Client(REMOTE_ADDR="95.24.7.15").get("/")
        self.assertEqual(blocked.status_code, 403)
        self.assertContains(blocked, "Доступ с вашего IP-адреса ограничен", status_code=403)
        self.assertEqual(Client(REMOTE_ADDR="95.24.8.15").get("/agreement/").status_code, 200)
        self.assertEqual(Client(REMOTE_ADDR="95.24.7.15").get("/health/").status_code, 200)

        admin_client = Client(REMOTE_ADDR="95.24.7.15")
        admin_client.force_login(self.admin)
        self.assertEqual(admin_client.get(reverse("control_center:multi_accounts")).status_code, 200)

    def test_lift_and_expiry(self):
        ban = services.create_ip_ban(actor=self.admin, network="95.24.9.9", reason="тест", duration="1h")
        self.assertTrue(services.is_ip_banned("95.24.9.9"))
        services.lift_ip_ban(actor=self.admin, ban=ban)
        self.assertFalse(services.is_ip_banned("95.24.9.9"))
        IpBan.objects.create(network="95.24.9.10/32", reason="old", expires_at=timezone.now() - timedelta(minutes=1))
        cache.clear()
        self.assertFalse(services.is_ip_banned("95.24.9.10"))

    def test_form_validation_and_own_ip_protection(self):
        client = Client(REMOTE_ADDR="95.24.11.11")
        client.force_login(self.admin)
        client.post(reverse("control_center:ip_ban_create"), {"network": "not-an-ip", "reason": "x", "duration": "1d"})
        client.post(reverse("control_center:ip_ban_create"), {"network": "95.24.11.0/24", "reason": "x", "duration": "1d"})
        self.assertFalse(IpBan.objects.exists())
        client.post(reverse("control_center:ip_ban_create"), {"network": "95.24.12.12", "reason": "x", "duration": "1d"})
        self.assertTrue(IpBan.objects.filter(network="95.24.12.12/32").exists())
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.IP_BAN).exists())
