from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription

from . import view_as

User = get_user_model()


def make_role(user, role):
    profile = UserProfile.objects.get(user=user)
    profile.role = role
    profile.save(update_fields=["role"])


class ViewAsUserTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="va_admin", password="StrongPass123", is_staff=True)
        make_role(self.admin, UserRole.ADMIN)
        self.client = Client()
        self.client.force_login(self.admin)
        self.dashboard_url = reverse("frontend:dashboard_preview")
        self.enter_url = reverse("accounts:view_as_user_enter")
        self.exit_url = reverse("accounts:view_as_user_exit")

    def test_admin_dashboard_shows_the_button(self):
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, self.enter_url)
        self.assertNotContains(response, "view-as-banner")

    def test_enter_shows_the_user_dashboard_with_exit_banner(self):
        response = self.client.post(self.enter_url, follow=True)
        self.assertEqual(response.context["dashboard_mode"], "user")
        self.assertContains(response, "view-as-banner")
        self.assertContains(response, self.exit_url)
        self.assertNotContains(response, self.enter_url)

    def test_exit_restores_admin_view(self):
        self.client.post(self.enter_url)
        response = self.client.post(self.exit_url, follow=True)
        self.assertEqual(response.context["dashboard_mode"], "team")
        self.assertNotContains(response, "view-as-banner")
        self.assertNotIn(view_as.SESSION_KEY, self.client.session)

    def test_admin_only_pages_are_closed_in_view_mode(self):
        self.client.post(self.enter_url)
        response = self.client.get(reverse("control_center:users"))
        self.assertIn(response.status_code, (302, 403, 404))

    def test_writes_are_blocked_but_nothing_is_lost(self):
        self.client.post(self.enter_url)
        response = self.client.post(reverse("accounts:security"), {"action": "end_others"})
        self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)
        self.assertEqual(UserProfile.objects.get(user=self.admin).role, UserRole.ADMIN)

    def test_masked_save_keeps_real_privileges(self):
        masked = view_as.build_masked_user(User.objects.get(pk=self.admin.pk))
        self.assertFalse(masked.is_staff)
        self.assertEqual(masked.profile_v2.role, UserRole.USER)
        masked.first_name = "Fedor"
        masked.save()
        masked.profile_v2.save()
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Fedor")
        self.assertTrue(self.admin.is_staff)
        self.assertEqual(UserProfile.objects.get(user=self.admin).role, UserRole.ADMIN)
        self.assertFalse(masked.is_staff)

    def test_subscription_is_hidden_in_view_mode(self):
        plan = SubscriptionPlan.objects.filter(family=SubscriptionPlan.Family.SITE, is_active=True).first()
        UserSubscription.objects.create(user=self.admin, plan=plan)
        self.client.post(self.enter_url)
        response = self.client.get(self.dashboard_url)
        self.assertFalse(response.context["subscription_context"].has_active_subscription)

    def test_regular_user_cannot_enter(self):
        user = User.objects.create_user(username="va_user", password="StrongPass123")
        client = Client()
        client.force_login(user)
        response = client.post(self.enter_url)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(view_as.SESSION_KEY, client.session)

    def test_stale_flag_is_dropped_when_admin_rights_are_gone(self):
        self.client.post(self.enter_url)
        make_role(self.admin, UserRole.USER)
        User.objects.filter(pk=self.admin.pk).update(is_staff=False)
        response = self.client.get(self.dashboard_url)
        self.assertNotContains(response, "view-as-banner")
        self.assertNotIn(view_as.SESSION_KEY, self.client.session)
