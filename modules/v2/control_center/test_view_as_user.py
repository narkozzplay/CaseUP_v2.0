from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.view_as_user import SESSION_KEY, build_regular_user_view

User = get_user_model()
PASSWORD = "StrongPass123"


def make_user(username: str, role: str = UserRole.USER, **extra):
    user = User.objects.create_user(username=username, password=PASSWORD, **extra)
    profile = UserProfile.objects.get(user=user)
    profile.role = role
    profile.save(update_fields=["role"])
    return user


class ViewAsUserTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", UserRole.SUPER_ADMIN, is_staff=True, is_superuser=True)
        self.client.login(username="boss", password=PASSWORD)

    def test_button_is_on_the_admin_dashboard_only(self):
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertContains(response, reverse("control_center:view_as_user_start"))

        make_user("plain")
        self.client.login(username="plain", password=PASSWORD)
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertNotContains(response, reverse("control_center:view_as_user_start"))
        self.assertEqual(self.client.post(reverse("control_center:view_as_user_start")).status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_admin_sees_the_regular_user_site_and_can_go_back(self):
        response = self.client.post(reverse("control_center:view_as_user_start"))
        self.assertRedirects(response, reverse("frontend:dashboard_preview"), fetch_redirect_response=False)

        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.context["dashboard_mode"], "user")
        self.assertFalse(response.context["user"].is_superuser)
        self.assertEqual(get_user_role(response.context["user"]).code, UserRole.USER)
        self.assertContains(response, "view-as-bar")
        self.assertContains(response, reverse("control_center:view_as_user_exit"))
        self.assertEqual(self.client.get(reverse("control_center:users")).status_code, 403)

        response = self.client.post(reverse("control_center:view_as_user_exit"))
        self.assertRedirects(response, reverse("frontend:dashboard_preview"), fetch_redirect_response=False)
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.context["dashboard_mode"], "team")
        self.assertNotContains(response, "view-as-bar")

    def test_preview_never_changes_the_stored_account(self):
        self.client.post(reverse("control_center:view_as_user_start"))
        self.client.get(reverse("frontend:dashboard_preview"))

        viewer = build_regular_user_view(User.objects.get(pk=self.admin.pk))
        viewer.save()
        viewer.profile_v2.save()
        viewer.profile_v2.save(update_fields=["role"])
        self.assertFalse(viewer.is_superuser)
        self.assertEqual(viewer.profile_v2.role, UserRole.USER)

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)
        self.assertTrue(self.admin.is_staff)
        self.assertEqual(UserProfile.objects.get(user=self.admin).role, UserRole.SUPER_ADMIN)

    def test_flag_is_dropped_for_someone_who_is_no_longer_admin(self):
        moder = make_user("moder", UserRole.MOD)
        self.client.login(username="moder", password=PASSWORD)
        session = self.client.session
        session[SESSION_KEY] = True
        session.save()
        response = self.client.get(reverse("frontend:dashboard_preview"))
        self.assertEqual(response.context["dashboard_mode"], "team")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(UserProfile.objects.get(user=moder).role, UserRole.MOD)
