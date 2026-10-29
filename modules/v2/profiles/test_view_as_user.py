from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.subscriptions.services import get_active_subscription

from .models import UserProfile, UserRole
from .rbac import get_user_role
from .view_as_user import SESSION_KEY, apply_view_as_user, is_viewing_as_user


User = get_user_model()
PASSWORD = "StrongPass123"


class ViewAsUserTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="vau_admin", password=PASSWORD, is_staff=True, is_superuser=True)
        profile = UserProfile.objects.get(user=self.admin)
        profile.role = UserRole.SUPER_ADMIN
        profile.save(update_fields=["role"])
        self.client.login(username="vau_admin", password=PASSWORD)

    def _start(self):
        return self.client.post(reverse("accounts:view_as_user_start"), {"next": reverse("frontend:dashboard_preview")})

    def test_team_dashboard_shows_button(self):
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertContains(response, reverse("accounts:view_as_user_start"))
        self.assertContains(response, "data-ops-dashboard")

    def test_regular_user_has_no_button_and_cannot_start(self):
        User.objects.create_user(username="vau_user", password=PASSWORD)
        self.client.login(username="vau_user", password=PASSWORD)

        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertNotContains(response, reverse("accounts:view_as_user_start"))

        response = self._start()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_start_switches_site_to_regular_user_view(self):
        response = self._start()
        self.assertRedirects(response, reverse("frontend:dashboard_preview"), fetch_redirect_response=False)

        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertContains(response, "data-user-dashboard")
        self.assertNotContains(response, "data-ops-dashboard")
        self.assertContains(response, "view-as-user-bar")
        self.assertContains(response, reverse("accounts:view_as_user_exit"))

        self.assertEqual(self.client.get(reverse("frontend:admin_panel")).status_code, 403)

    def test_actions_are_blocked_but_exit_restores_access(self):
        self._start()

        response = self.client.post(reverse("accounts:view_as_user_start"))
        self.assertEqual(response.status_code, 403)

        response = self.client.post(reverse("accounts:view_as_user_exit"), {"next": reverse("frontend:admin_panel")})
        self.assertRedirects(response, reverse("frontend:admin_panel"), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("frontend:admin_panel")).status_code, 200)

    def test_demoted_user_drops_the_mode(self):
        self._start()
        User.objects.filter(pk=self.admin.pk).update(is_staff=False, is_superuser=False)
        UserProfile.objects.filter(user=self.admin).update(role=UserRole.USER)

        self.client.get(reverse("frontend:dashboard_preview"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_real_role_is_never_saved_while_viewing(self):
        user = User.objects.get(pk=self.admin.pk)
        apply_view_as_user(user)
        self.assertTrue(is_viewing_as_user(user))
        self.assertFalse(get_user_role(user).is_moder_plus)

        user.save()
        user.profile_v2.save()
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.profile_v2.role, UserRole.USER)

        fresh = User.objects.get(pk=self.admin.pk)
        self.assertTrue(fresh.is_superuser)
        self.assertTrue(fresh.is_staff)
        self.assertEqual(fresh.profile_v2.role, UserRole.SUPER_ADMIN)

    def test_active_subscription_is_hidden(self):
        plan = SubscriptionPlan.objects.filter(is_active=True).first()
        UserSubscription.objects.create(user=self.admin, plan=plan, family=plan.family)

        user = User.objects.get(pk=self.admin.pk)
        self.assertIsNotNone(get_active_subscription(user, family=plan.family))
        apply_view_as_user(user)
        self.assertIsNone(get_active_subscription(user, family=plan.family))
