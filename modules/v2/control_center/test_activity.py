from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance

from .models import ActivityCategory, ActivityLog
from .services.activity import log_activity, prune_activity_log
from .services.users import ban_user

User = get_user_model()
PASSWORD = "StrongPass123"


def make_user(username: str, role: str = UserRole.USER):
    user = User.objects.create_user(username=username, password=PASSWORD)
    if role != UserRole.USER:
        profile = UserProfile.objects.get(user=user)
        profile.role = role
        profile.save(update_fields=["role"])
        user = User.objects.get(pk=user.pk)
    return user


def events(**filters):
    return list(ActivityLog.objects.filter(**filters).order_by("id").values_list("event", flat=True))


class ActivityReceiverTests(TestCase):
    def setUp(self):
        self.user = make_user("player")

    def test_registration_is_logged(self):
        entry = ActivityLog.objects.get(event="account.registered", user=self.user)
        self.assertEqual(entry.category, ActivityCategory.ACCOUNT)
        self.assertEqual(entry.username, "player")

    def test_login_logout_and_failed_login(self):
        self.client.post(reverse("admin:login"), {"username": "player", "password": "wrong-password"})
        failed = ActivityLog.objects.get(event="auth.login_failed")
        self.assertEqual(failed.payload, {"username": "player"})
        self.assertNotIn("wrong-password", str(failed.payload) + failed.summary)
        self.assertEqual(failed.ip_address, "127.0.0.1")

        self.client.login(username="player", password=PASSWORD)
        self.client.logout()
        self.assertEqual(events(user=self.user, category=ActivityCategory.AUTH), ["auth.login", "auth.logout"])

    def test_balance_change_is_logged_with_actor(self):
        admin = make_user("cashier", UserRole.ADMIN)
        adjust_balance(user=self.user, delta=Decimal("150"), reason=LedgerReason.ADMIN_ADJUST, comment="бонус", created_by=admin)
        entry = ActivityLog.objects.get(event="balance.admin_adjust")
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.actor, admin)
        self.assertEqual(entry.amount, Decimal("150.00"))
        self.assertIn("бонус", entry.summary)

    def test_payment_created_and_paid(self):
        from modules.v2.payments.models import Payment

        payment = Payment.objects.create(user=self.user, provider="yookassa", amount=Decimal("500.00"))
        payment = Payment.objects.get(pk=payment.pk)
        payment.status = Payment.Status.PENDING
        payment.save()
        payment.status = Payment.Status.SUCCEEDED
        payment.save()
        self.assertEqual(events(category=ActivityCategory.PAYMENT), ["payment.created", "payment.succeeded"])
        paid = ActivityLog.objects.get(event="payment.succeeded")
        self.assertEqual(paid.payload["from"], "pending")
        self.assertIn("Ожидает оплаты → Оплачен", paid.summary)

    def test_case_open_and_sell(self):
        from modules.v2.case_simulator import services
        from modules.v2.case_simulator.tests import _make_case

        UserProfile.objects.filter(user=self.user).update(balance_coins=Decimal("1000"))
        case = _make_case()
        [opening] = services.open_case(user=self.user, case=case)
        services.sell_openings(user=self.user, opening_ids=[opening.pk])

        opened = ActivityLog.objects.get(event="case.open")
        self.assertEqual(opened.user, self.user)
        self.assertEqual(opened.object_id, str(opening.pk))
        self.assertIn(opening.item.name, opened.summary)
        sold = ActivityLog.objects.get(event="case.item_sold")
        self.assertEqual(sold.amount, opening.item_value.quantize(Decimal("0.01")))
        self.assertTrue(ActivityLog.objects.filter(event="balance.case_open", user=self.user).exists())
        self.assertTrue(ActivityLog.objects.filter(event="balance.item_sale", user=self.user).exists())

    def test_social_actions(self):
        from modules.v2.social_friends import services

        friend = make_user("friend")
        request = services.send_friend_request(self.user, friend)
        services.accept_friend_request(friend, request.pk)
        services.remove_friend(self.user, friend)
        services.block_user(self.user, friend)
        services.unblock_user(self.user, friend)
        self.assertEqual(
            events(category=ActivityCategory.SOCIAL),
            ["social.friend_request", "social.friend_request_status", "social.friend_removed", "social.user_blocked", "social.user_unblocked"],
        )
        self.assertEqual(ActivityLog.objects.get(event="social.friend_request_status").user, friend)

    def test_privacy_change_is_logged_but_creation_is_not(self):
        from modules.v2.social_friends.services import update_privacy_settings

        update_privacy_settings(self.user, who_can_see_online="nobody")
        update_privacy_settings(self.user, who_can_see_online="nobody")
        [entry] = ActivityLog.objects.filter(event="account.privacy")
        self.assertEqual(entry.payload["who_can_see_online"]["to"], "nobody")

    def test_ban_logs_security_and_admin_events(self):
        admin = make_user("moder", UserRole.ADMIN)
        ban_user(actor=admin, target=self.user, reason="читы", duration="1d")
        ban = ActivityLog.objects.get(event="security.ban")
        self.assertEqual((ban.user, ban.actor), (self.user, admin))
        self.assertTrue(ActivityLog.objects.filter(event="admin.user_ban", user=self.user, actor=admin).exists())

    def test_deferred_fields_are_not_loaded_by_snapshot(self):
        from modules.v2.payments.models import Payment

        Payment.objects.create(user=self.user, provider="yookassa", amount=Decimal("10.00"))
        with CaptureQueriesContext(connection) as ctx:
            list(Payment.objects.only("id", "amount"))
        self.assertEqual(len(ctx.captured_queries), 1)

    def test_journal_failure_never_breaks_the_action(self):
        with mock.patch.object(ActivityLog.objects, "create", side_effect=RuntimeError("journal down")):
            entry = adjust_balance(user=self.user, delta=Decimal("5"), reason=LedgerReason.ADMIN_ADJUST)
        self.assertTrue(LedgerEntry.objects.filter(pk=entry.pk).exists())
        # The surrounding transaction is still usable afterwards.
        self.assertEqual(User.objects.filter(pk=self.user.pk).count(), 1)

    def test_broken_receiver_never_breaks_the_action(self):
        with mock.patch("modules.v2.control_center.activity_events.log_activity", side_effect=RuntimeError("boom")):
            entry = adjust_balance(user=self.user, delta=Decimal("5"), reason=LedgerReason.ADMIN_ADJUST)
        self.assertTrue(LedgerEntry.objects.filter(pk=entry.pk).exists())


class ActivityMiddlewareTests(TestCase):
    def setUp(self):
        self.user = make_user("clicker")
        self.client.force_login(self.user)
        ActivityLog.objects.all().delete()

    def test_state_changing_request_is_logged_without_body(self):
        self.client.post(reverse("accounts:logout"), {"secret": "do-not-store"}, HTTP_USER_AGENT="TestAgent/1.0")
        entry = ActivityLog.objects.get(category=ActivityCategory.REQUEST)
        self.assertEqual(entry.method, "POST")
        self.assertEqual(entry.status_code, 302)
        self.assertEqual(entry.user_agent, "TestAgent/1.0")
        self.assertEqual(entry.user, self.user)
        self.assertNotIn("do-not-store", str(entry.payload) + entry.summary + entry.path)

    def test_safe_requests_and_webhooks_are_not_logged(self):
        self.client.get(reverse("frontend:home"))
        self.client.post("/payments/webhook/yookassa/", data="{}", content_type="application/json")
        self.assertFalse(ActivityLog.objects.filter(category=ActivityCategory.REQUEST).exists())


class ActivityPruneTests(TestCase):
    @override_settings(ACTIVITY_LOG_REQUEST_RETENTION_DAYS=30, ACTIVITY_LOG_RETENTION_DAYS=100)
    def test_prune_keeps_recent_and_respects_category_retention(self):
        ActivityLog.objects.all().delete()
        now = timezone.now()
        old_request = log_activity(category=ActivityCategory.REQUEST, event="request.x")
        kept_event = log_activity(category=ActivityCategory.CASE, event="case.open")
        old_event = log_activity(category=ActivityCategory.CASE, event="case.open")
        ActivityLog.objects.filter(pk=old_request.pk).update(created_at=now - timedelta(days=31))
        ActivityLog.objects.filter(pk=kept_event.pk).update(created_at=now - timedelta(days=60))
        ActivityLog.objects.filter(pk=old_event.pk).update(created_at=now - timedelta(days=101))

        self.assertEqual(prune_activity_log(now=now), {"requests": 1, "events": 1})
        self.assertEqual(list(ActivityLog.objects.values_list("pk", flat=True)), [kept_event.pk])


class ActivityTabTests(TestCase):
    def setUp(self):
        self.admin = make_user("cc_logs_admin", UserRole.ADMIN)
        self.player = make_user("cc_logs_player")
        log_activity(category=ActivityCategory.CASE, event="case.open", summary="Открыл кейс", user=self.player, amount=Decimal("-100"), payload={"item": 7})
        log_activity(category=ActivityCategory.REQUEST, event="request.x", summary="POST x → 200", user=self.player, method="POST", path="/x/", status_code=200)
        self.client.force_login(self.admin)

    def test_regular_user_has_no_access(self):
        self.client.force_login(self.player)
        self.assertEqual(self.client.get(reverse("control_center:activity")).status_code, 403)
        self.assertEqual(self.client.get(reverse("control_center:activity_export")).status_code, 403)

    def test_feed_hides_raw_requests_by_default(self):
        response = self.client.get(reverse("control_center:activity"))
        self.assertContains(response, "Логи")
        self.assertContains(response, "Открыл кейс")
        self.assertContains(response, "-100.00 ₽")
        self.assertNotContains(response, "POST x → 200")

        response = self.client.get(reverse("control_center:activity"), {"requests": "1"})
        self.assertContains(response, "POST x → 200")

    def test_filters(self):
        url = reverse("control_center:activity")
        page = lambda **params: [entry.event for entry in self.client.get(url, params).context["page_obj"]]  # noqa: E731
        self.assertEqual(page(category="case"), ["case.open"])
        self.assertEqual(page(event="case.open", q="cc_logs_player"), ["case.open"])
        self.assertEqual(page(q="nobody-like-this"), [])
        self.assertEqual(page(user=str(self.player.pk), requests="1", event="request.x"), ["request.x"])
        self.assertIn("account.registered", page(user=str(self.player.pk)))
        self.assertNotIn("account.registered", page(user=str(self.admin.pk), category="case"))

    def test_csv_export(self):
        response = self.client.get(reverse("control_center:activity_export"), {"category": "case"})
        body = response.content.decode("utf-8")
        self.assertIn("case.open", body)
        self.assertIn("Открыл кейс", body)
        self.assertIn('{""item"": 7}', body)
        self.assertNotIn("request.x", body)

    def test_user_detail_links_to_activity(self):
        response = self.client.get(reverse("control_center:user_detail", args=[self.player.pk]))
        self.assertContains(response, f"{reverse('control_center:activity')}?user={self.player.pk}")
