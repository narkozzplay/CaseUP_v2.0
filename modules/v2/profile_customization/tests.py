from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription

from .models import SteamAvatar, SteamBackground, UserCustomization
from .services import (
    add_avatar,
    add_background,
    can_customize_profile,
    clear_avatar,
    clear_background,
    delete_avatar,
    delete_background,
    get_or_create_customization,
    list_avatars,
    list_backgrounds,
    set_avatar,
    set_background,
)


User = get_user_model()
PARTIAL_HEADERS = {"HTTP_X_DASHBOARD_PARTIAL": "1"}


def _make_user(username: str) -> User:
    return User.objects.create_user(username=username, password="pass")


def _make_admin(username: str) -> User:
    u = _make_user(username)
    u.profile_v2.role = "admin"
    u.profile_v2.save(update_fields=["role"])
    return u


class CanCustomizeTests(TestCase):
    def setUp(self):
        self.regular = _make_user("regular_cust")
        self.admin = _make_admin("admin_cust")
        self.store_author = _make_user("author_cust")
        self.store_author.profile_v2.is_store_author = True
        self.store_author.profile_v2.save(update_fields=["is_store_author"])

    def test_regular_user_cannot(self):
        self.assertFalse(can_customize_profile(self.regular))

    def test_admin_can(self):
        self.assertTrue(can_customize_profile(self.admin))

    def test_store_author_can(self):
        self.assertTrue(can_customize_profile(self.store_author))

    def test_level5_can(self):
        plan = SubscriptionPlan.objects.get(level=5)
        UserSubscription.objects.create(user=self.regular, plan=plan, family="site")
        self.assertTrue(can_customize_profile(self.regular))

    def test_level4_cannot(self):
        plan = SubscriptionPlan.objects.get(level=4)
        UserSubscription.objects.create(user=self.regular, plan=plan, family="site")
        self.assertFalse(can_customize_profile(self.regular))

    def test_anon_cannot(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(can_customize_profile(AnonymousUser()))


class BackgroundServiceTests(TestCase):
    def setUp(self):
        self.admin = _make_admin("admin_bg")
        self.regular = _make_user("regular_bg")
        # Give regular user level 5 access
        plan = SubscriptionPlan.objects.get(level=5)
        UserSubscription.objects.create(user=self.regular, plan=plan, family="site")

    def test_add_background(self):
        bg = add_background(
            self.admin,
            title="Test BG",
            static_url="https://example.com/bg.jpg",
            video_url="https://example.com/bg.webm",
        )
        self.assertEqual(bg.title, "Test BG")
        self.assertEqual(bg.is_active, True)

    def test_add_background_extracts_item_id(self):
        bg = add_background(
            self.admin,
            title="Steam BG",
            static_url="https://shared.fastly.steamstatic.com/community_assets/images/items/2861720/abc.jpg",
            video_url="https://shared.fastly.steamstatic.com/community_assets/images/items/2861720/xyz.webm",
        )
        self.assertEqual(bg.source_item_id, "2861720")

    def test_non_admin_cannot_add_background(self):
        with self.assertRaises(PermissionError):
            add_background(self.regular, title="X", static_url="http://x.jpg", video_url="http://x.webm")

    def test_set_background_updates_profile_cache(self):
        bg = add_background(self.admin, title="BG", static_url="http://s.jpg", video_url="http://v.webm")
        set_background(self.regular, bg.pk)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_bg_static_url, "http://s.jpg")
        self.assertEqual(profile.selected_bg_video_url, "http://v.webm")
        customization = UserCustomization.objects.get(user=self.regular)
        self.assertEqual(customization.selected_background, bg)

    def test_clear_background_clears_profile_cache(self):
        bg = add_background(self.admin, title="BG2", static_url="http://s2.jpg", video_url="http://v2.webm")
        set_background(self.regular, bg.pk)
        clear_background(self.regular)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_bg_static_url, "")
        self.assertEqual(profile.selected_bg_video_url, "")

    def test_delete_background_clears_all_selections(self):
        bg = add_background(self.admin, title="BG3", static_url="http://s3.jpg", video_url="http://v3.webm")
        set_background(self.regular, bg.pk)
        delete_background(self.admin, bg.pk)
        # Profile cache cleared
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_bg_static_url, "")
        # Customization cleared
        customization = UserCustomization.objects.get(user=self.regular)
        self.assertIsNone(customization.selected_background)

    def test_regular_without_access_cannot_set(self):
        no_access = _make_user("no_access_bg")
        bg = add_background(self.admin, title="BG4", static_url="http://x.jpg", video_url="http://x.webm")
        with self.assertRaises(PermissionError):
            set_background(no_access, bg.pk)


class AvatarServiceTests(TestCase):
    def setUp(self):
        self.admin = _make_admin("admin_av")
        self.regular = _make_user("regular_av")
        plan = SubscriptionPlan.objects.get(level=5)
        UserSubscription.objects.create(user=self.regular, plan=plan, family="site")

    def test_add_avatar(self):
        av = add_avatar(self.admin, title="Steam AV", avatar_url="https://avatars.steamstatic.com/abc_full.jpg")
        self.assertEqual(av.title, "Steam AV")

    def test_set_avatar_updates_profile(self):
        av = add_avatar(self.admin, title="AV1", avatar_url="https://example.com/av.jpg")
        set_avatar(self.regular, av.pk)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_avatar_url, "https://example.com/av.jpg")

    def test_clear_avatar(self):
        av = add_avatar(self.admin, title="AV2", avatar_url="https://example.com/av2.jpg")
        set_avatar(self.regular, av.pk)
        clear_avatar(self.regular)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_avatar_url, "")

    def test_delete_avatar_clears_selections(self):
        av = add_avatar(self.admin, title="AV3", avatar_url="https://example.com/av3.jpg")
        set_avatar(self.regular, av.pk)
        delete_avatar(self.admin, av.pk)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(profile.selected_avatar_url, "")

    def test_avatar_reflected_in_get_profile_avatar_url(self):
        from modules.v2.profiles.presentation import get_profile_avatar_url
        av = add_avatar(self.admin, title="AV4", avatar_url="https://example.com/catalog_av.jpg")
        set_avatar(self.regular, av.pk)
        profile = UserProfile.objects.get(user=self.regular)
        self.assertEqual(get_profile_avatar_url(profile), "https://example.com/catalog_av.jpg")


class CatalogViewTests(TestCase):
    def setUp(self):
        self.admin = _make_admin("admin_view")
        self.regular = _make_user("regular_view")
        self.client = Client()

    def test_catalog_view_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profile_customization:catalog"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_catalog_view_regular(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("profile_customization:catalog"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_admin_add_background_get(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profile_customization:admin_add_background"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_admin_add_background_forbidden_for_regular(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("profile_customization:admin_add_background"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 403)

    def test_admin_add_background_post(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("profile_customization:admin_add_background"),
            {
                "title": "Test Background",
                "static_url": "https://example.com/test.jpg",
                "video_url": "https://example.com/test.webm",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(SteamBackground.objects.filter(title="Test Background").exists())

    def test_select_background_post_requires_access(self):
        bg = add_background(self.admin, title="Sel BG", static_url="http://s.jpg", video_url="http://v.webm")
        self.client.force_login(self.regular)
        resp = self.client.post(
            reverse("profile_customization:select_background", args=[bg.pk]),
            {"next": reverse("profile_customization:catalog")},
        )
        # Regular without Level 5 → permission error (redirect with message)
        self.assertEqual(resp.status_code, 302)

    def test_views_require_login(self):
        anon = Client()
        urls = [
            reverse("profile_customization:catalog"),
            reverse("profile_customization:admin_add_background"),
            reverse("profile_customization:admin_add_avatar"),
        ]
        for url in urls:
            resp = anon.get(url)
            self.assertIn(resp.status_code, [302, 403])
