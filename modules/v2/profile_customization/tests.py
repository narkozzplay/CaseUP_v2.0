from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription

from .models import ShowcaseItem, SteamAvatar, SteamBackground, UserCustomization
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
        plan = SubscriptionPlan.objects.get(code="site_pro")
        UserSubscription.objects.create(user=self.regular, plan=plan, family="site")
        self.assertTrue(can_customize_profile(self.regular))

    def test_level4_cannot(self):
        plan = SubscriptionPlan.objects.get(code="site_plus")
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
        plan = SubscriptionPlan.objects.get(code="site_pro")
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
        plan = SubscriptionPlan.objects.get(code="site_pro")
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


class CatalogPolishTests(TestCase):
    def setUp(self):
        self.admin = _make_admin("admin_polish")
        self.regular = _make_user("regular_polish")
        self.client = Client()

    def test_background_without_video_is_allowed(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("profile_customization:admin_add_background"),
            {"title": "Static BG", "static_url": "https://example.com/static.jpg", "video_url": ""},
        )
        self.assertEqual(resp.status_code, 302)
        bg = SteamBackground.objects.get(title="Static BG")
        self.assertEqual(bg.video_url, "")

    def test_select_redirect_ignores_external_next(self):
        bg = add_background(self.admin, title="Ext", static_url="https://example.com/e.jpg")
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("profile_customization:select_background", args=[bg.pk]),
            {"next": "https://evil.example.com/steal"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("profile_customization:catalog") + "?tab=backgrounds")

    def test_select_redirect_keeps_local_next(self):
        bg = add_background(self.admin, title="Local", static_url="https://example.com/l.jpg")
        self.client.force_login(self.admin)
        local = reverse("profile_customization:catalog") + "?tab=avatars"
        resp = self.client.post(reverse("profile_customization:select_background", args=[bg.pk]), {"next": local})
        self.assertEqual(resp["Location"], local)

    def test_restore_background(self):
        bg = add_background(self.admin, title="Hide me", static_url="https://example.com/h.jpg")
        delete_background(self.admin, bg.pk)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("profile_customization:admin_restore_background", args=[bg.pk]))
        self.assertEqual(resp.status_code, 302)
        bg.refresh_from_db()
        self.assertTrue(bg.is_active)

    def test_restore_forbidden_for_regular(self):
        av = add_avatar(self.admin, title="Hidden av", avatar_url="https://example.com/h.jpg")
        delete_avatar(self.admin, av.pk)
        self.client.force_login(self.regular)
        self.client.post(reverse("profile_customization:admin_restore_avatar", args=[av.pk]))
        av.refresh_from_db()
        self.assertFalse(av.is_active)

    def test_hidden_tab_only_for_admin(self):
        av = add_avatar(self.admin, title="Gone avatar", avatar_url="https://example.com/g.jpg")
        delete_avatar(self.admin, av.pk)
        url = reverse("profile_customization:catalog") + "?tab=hidden"

        self.client.force_login(self.admin)
        resp = self.client.get(url, **PARTIAL_HEADERS)
        self.assertContains(resp, "Gone avatar")

        self.client.force_login(self.regular)
        resp = self.client.get(url, **PARTIAL_HEADERS)
        self.assertEqual(resp.context["active_tab"], "effects")
        self.assertNotContains(resp, "Gone avatar")

    def test_locked_user_sees_plans_cta_and_no_apply(self):
        add_background(self.admin, title="Locked BG", static_url="https://example.com/lb.jpg")
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("profile_customization:catalog") + "?tab=backgrounds", **PARTIAL_HEADERS)
        self.assertContains(resp, "Locked BG")
        self.assertContains(resp, reverse("subscriptions:plans"))
        bg_pk = SteamBackground.objects.get(title="Locked BG").pk
        self.assertNotContains(resp, 'action="' + reverse("profile_customization:select_background", args=[bg_pk]))
        self.assertFalse(resp.context["can_customize"])

    def test_current_selection_shown(self):
        bg = add_background(self.admin, title="Mine BG", static_url="https://example.com/m.jpg", video_url="https://example.com/m.webm")
        set_background(self.admin, bg.pk)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profile_customization:catalog") + "?tab=backgrounds", **PARTIAL_HEADERS)
        self.assertEqual(resp.context["current_bg_video"], "https://example.com/m.webm")
        self.assertContains(resp, "pcz-card--bg is-current")

    def test_menu_item_visible_to_regular_user(self):
        from django.test import RequestFactory

        from modules.v2.system.dashboard_menu import build_dashboard_menu

        request = RequestFactory().get(reverse("profile_customization:catalog"))
        request.user = self.regular
        request.LANGUAGE_CODE = "ru"
        keys = [item["key"] if isinstance(item, dict) else item.key for group in build_dashboard_menu(request) for item in group["items"]]
        self.assertIn("customization_catalog", keys)

    def test_settings_card_hidden_when_admin_views_other_user(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profiles:profile_user_settings", args=[self.regular.pk]), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="settings-customization"')

        resp = self.client.get(reverse("profiles:profile_settings"), **PARTIAL_HEADERS)
        self.assertContains(resp, 'id="settings-customization"')


class ProfileEffectsTests(TestCase):
    def setUp(self):
        self.admin = _make_admin("admin_fx")
        self.regular = _make_user("regular_fx")
        self.client = Client()

    def test_set_effect_requires_access(self):
        from .services import set_effect

        with self.assertRaises(PermissionError):
            set_effect(self.regular, "frame", "neon")

    def test_set_effect_rejects_unknown_key_and_slot(self):
        from .services import set_effect

        with self.assertRaises(ValueError):
            set_effect(self.admin, "frame", "does-not-exist")
        with self.assertRaises(ValueError):
            set_effect(self.admin, "shoes", "neon")

    def test_select_and_clear_effect_view(self):
        self.client.force_login(self.admin)
        url = reverse("profile_customization:select_effect", args=["name"])
        resp = self.client.post(url, {"key": "glitch"})
        self.assertEqual(resp["Location"], reverse("profile_customization:catalog") + "?tab=effects")
        self.assertEqual(get_or_create_customization(self.admin).name_effect, "glitch")

        self.client.post(url, {"key": ""})
        self.assertEqual(get_or_create_customization(self.admin).name_effect, "")

    def test_profile_shows_effect_classes(self):
        from .services import set_effect

        set_effect(self.admin, "frame", "gold")
        set_effect(self.admin, "hero", "embers")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profiles:profile"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "pfx-frame--gold")
        self.assertContains(resp, "pfx-hero--embers")
        self.assertNotContains(resp, "pfx-name--")

    def test_effects_hidden_when_access_is_lost(self):
        from .services import get_profile_effect_classes, set_effect

        set_effect(self.admin, "frame", "neon")
        self.admin.profile_v2.role = "user"
        self.admin.profile_v2.save(update_fields=["role"])
        self.assertEqual(get_profile_effect_classes(self.admin)["frame"], "")

    def test_effects_tab_is_default(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("profile_customization:catalog"), **PARTIAL_HEADERS)
        self.assertEqual(resp.context["active_tab"], "effects")
        self.assertContains(resp, "pfx-frame--holo")


class EffectCollectionTests(TestCase):
    def setUp(self):
        self.regular = _make_user("regular_coll")
        self.admin = _make_admin("admin_coll")
        self.client = Client()

    def _pro(self, user):
        plan = SubscriptionPlan.objects.get(code="site_pro")
        UserSubscription.objects.create(user=user, plan=plan, family="site")

    def test_free_effects_need_no_plan(self):
        from .services import get_profile_look, set_effect

        set_effect(self.regular, "frame", "steel")
        set_effect(self.regular, "title", "cs2_player")
        set_effect(self.regular, "accent", "blue")
        look = get_profile_look(self.regular)
        self.assertEqual(look["frame"], "pfx-frame--steel")
        self.assertEqual(look["accent"], "pfx-accent--blue")
        self.assertEqual(look["title"].key, "cs2_player")

    def test_pro_effect_needs_pro(self):
        from .services import set_effect

        with self.assertRaises(PermissionError):
            set_effect(self.regular, "name", "fade")
        self._pro(self.regular)
        set_effect(self.regular, "name", "fade")

    def test_reward_needs_a_grant_even_with_pro(self):
        from .services import get_profile_look, grant_effect, set_effect

        self._pro(self.regular)
        with self.assertRaises(PermissionError):
            set_effect(self.regular, "frame", "case_hardened")
        grant = grant_effect(self.regular, "frame", "case_hardened", source="test")
        self.assertIsNotNone(grant)
        self.assertIsNone(grant_effect(self.regular, "frame", "case_hardened", source="test"))
        set_effect(self.regular, "frame", "case_hardened")
        self.assertEqual(get_profile_look(self.regular)["frame"], "pfx-frame--case_hardened")
        # Taken back by an admin: the profile stops showing it.
        grant.delete()
        self.assertEqual(get_profile_look(self.regular)["frame"], "")

    def test_grant_sends_a_notification(self):
        from modules.v2.notifications.models import Notification

        from .services import grant_effect

        grant_effect(self.regular, "title", "veteran")
        self.assertTrue(Notification.objects.filter(recipient=self.regular, kind="profile_effect_unlocked").exists())

    def test_admin_wears_rewards(self):
        from .services import set_effect

        set_effect(self.admin, "hero", "dust2")

    def test_pro_effects_hide_when_pro_ends_free_ones_stay(self):
        from .services import get_profile_look, set_effect

        self._pro(self.regular)
        set_effect(self.regular, "frame", "doppler")
        set_effect(self.regular, "title", "skin_fan")
        UserSubscription.objects.filter(user=self.regular).delete()
        look = get_profile_look(self.regular)
        self.assertEqual(look["frame"], "")
        self.assertEqual(look["title"].key, "skin_fan")

    def test_achievement_from_daily_bonus_streak(self):
        from modules.v2.daily_bonus.models import DailyBonusState

        from .models import EffectGrant
        from .services import sync_achievements

        DailyBonusState.objects.create(user=self.regular, streak=0, best_streak=8)
        new = sync_achievements(self.regular)
        self.assertEqual([(g.slot, g.key) for g in new], [("title", "regular")])
        self.assertEqual(sync_achievements(self.regular), [])
        self.assertEqual(EffectGrant.objects.get(user=self.regular).source, "achievement:regular")

    def test_hourly_sweep_only_recent_users(self):
        from datetime import timedelta

        from django.utils import timezone

        from modules.v2.daily_bonus.models import DailyBonusState
        from modules.v2.profiles.models import UserProfile

        from .models import EffectGrant
        from .services import sync_recent_achievements

        other = _make_user("sweep_old")
        for user in (self.regular, other):
            DailyBonusState.objects.create(user=user, best_streak=8)
        UserProfile.objects.filter(user=self.regular).update(last_seen_at=timezone.now())
        UserProfile.objects.filter(user=other).update(last_seen_at=timezone.now() - timedelta(days=1))
        self.assertEqual(sync_recent_achievements(), 1)
        self.assertTrue(EffectGrant.objects.filter(user=self.regular, key="regular").exists())
        self.assertFalse(EffectGrant.objects.filter(user=other).exists())

    def test_catalog_syncs_and_marks_new(self):
        from modules.v2.daily_bonus.models import DailyBonusState

        from .models import EffectGrant

        DailyBonusState.objects.create(user=self.regular, best_streak=31)
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("profile_customization:catalog"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["new_effects_count"], 2)
        self.assertContains(resp, "pcz-flag--new")
        self.assertFalse(EffectGrant.objects.filter(user=self.regular, seen_at__isnull=True).exists())

    def test_kit(self):
        from .services import apply_kit

        apply_kit(self.regular, "recruit")
        customization = get_or_create_customization(self.regular)
        self.assertEqual((customization.avatar_frame, customization.name_effect, customization.accent_effect), ("steel", "milspec", "blue"))
        with self.assertRaises(PermissionError):
            apply_kit(self.regular, "fire")
        self.client.force_login(self.regular)
        self.client.post(reverse("profile_customization:apply_kit"), {"kit": "fire"})
        self.assertEqual(get_or_create_customization(self.regular).avatar_frame, "steel")

    def test_profile_shows_title_and_accent(self):
        from .services import set_effect

        set_effect(self.regular, "title", "skin_fan")
        set_effect(self.regular, "accent", "green")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("profiles:profile_detail", args=[self.regular.pk]), **PARTIAL_HEADERS)
        self.assertContains(resp, "pfx-title--milspec")
        self.assertContains(resp, "Ценитель скинов")
        self.assertContains(resp, "pfx-accent--green")

    def test_friends_list_shows_effects(self):
        from modules.v2.social_friends.models import Friendship

        from .services import set_effect

        set_effect(self.regular, "name", "milspec")
        set_effect(self.regular, "title", "cs2_player")
        low, high = sorted([self.regular, self.admin], key=lambda u: u.pk)
        Friendship.objects.create(user_a=low, user_b=high)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("social_friends:friends"), **PARTIAL_HEADERS)
        self.assertContains(resp, "pfx-name--milspec")
        self.assertContains(resp, "Игрок CS2")


class ShowcaseTests(TestCase):
    def setUp(self):
        from modules.v2.steam_catalog.models import SteamItem, SteamItemFavorite

        self.user = _make_user("showcase_user")
        self.items = []
        for index in range(6):
            item = SteamItem.objects.create(market_hash_name=f"Item {index}", name=f"Item {index}", item_type="skin", price_rub=100 + index)
            SteamItemFavorite.objects.create(user=self.user, family_key=f"fam-{index}", item=item)
            self.items.append(item)
        self.stranger_item = SteamItem.objects.create(market_hash_name="Other", name="Other", item_type="skin")

    def test_set_and_show_with_weekly_change(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from modules.v2.steam_catalog.models import SteamItemPriceHistory

        from .services import set_showcase, showcase_for

        set_showcase(self.user, [self.items[0].pk, self.items[1].pk])
        old = SteamItemPriceHistory.objects.create(item=self.items[0], price_rub=Decimal("80"))
        SteamItemPriceHistory.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))
        rows = showcase_for(self.user)
        self.assertEqual([row["item"].pk for row in rows], [self.items[0].pk, self.items[1].pk])
        self.assertEqual(rows[0]["change"], Decimal("25.0"))
        self.assertIsNone(rows[1]["change"])

    def test_limits(self):
        from .services import set_showcase

        with self.assertRaises(ValueError):
            set_showcase(self.user, [item.pk for item in self.items[:5]])
        with self.assertRaises(ValueError):
            set_showcase(self.user, [self.stranger_item.pk])

    def test_views(self):
        client = Client()
        client.force_login(self.user)
        resp = client.get(reverse("profile_customization:catalog") + "?tab=showcase", **PARTIAL_HEADERS)
        self.assertContains(resp, "data-pcz-showcase")
        client.post(reverse("profile_customization:save_showcase"), {"items": [self.items[2].pk]})
        resp = client.get(reverse("profiles:profile"), **PARTIAL_HEADERS)
        self.assertContains(resp, "pfx-showcase__item")
        self.assertContains(resp, "Item 2")

    def test_pinned_item_can_be_cleared_after_its_favorite_is_gone(self):
        from modules.v2.steam_catalog.models import SteamItemFavorite

        client = Client()
        client.force_login(self.user)
        client.post(reverse("profile_customization:save_showcase"), {"items": [self.items[0].pk]})
        SteamItemFavorite.objects.filter(user=self.user).delete()
        resp = client.get(reverse("profile_customization:catalog") + "?tab=showcase", **PARTIAL_HEADERS)
        self.assertContains(resp, "data-pcz-showcase")
        client.post(reverse("profile_customization:save_showcase"), {})
        self.assertFalse(ShowcaseItem.objects.filter(user=self.user).exists())


class PrimeLooksTests(TestCase):
    def test_many_users_in_a_fixed_number_of_queries(self):
        from modules.v2.system.request_cache import _request_cache

        from .services import get_profile_look, grant_effect, prime_looks, set_effect

        users = [_make_user(f"prime_{index}") for index in range(5)]
        for user in users:
            set_effect(user, "name", "milspec")
        grant_effect(users[0], "title", "veteran", send_notification=False)
        set_effect(users[0], "title", "veteran")
        token = _request_cache.set({})
        try:
            with self.assertNumQueries(2):
                prime_looks([user.pk for user in users])
            with self.assertNumQueries(0):
                looks = [get_profile_look(user.pk) for user in users]
        finally:
            _request_cache.reset(token)
        self.assertTrue(all(look["name"] == "pfx-name--milspec" for look in looks))
        self.assertEqual(looks[0]["title"].key, "veteran")

    def test_pro_looks_are_batched_too(self):
        from modules.v2.system.request_cache import _request_cache

        from .services import get_profile_look, prime_looks, set_effect

        plan = SubscriptionPlan.objects.get(code="site_pro")
        users = [_make_user(f"prime_pro_{index}") for index in range(5)]
        for user in users:
            UserSubscription.objects.create(user=user, plan=plan, family="site")
            set_effect(user, "frame", "doppler")
        token = _request_cache.set({})
        try:
            with self.assertNumQueries(3):
                prime_looks([user.pk for user in users])
            with self.assertNumQueries(0):
                looks = [get_profile_look(user.pk) for user in users]
        finally:
            _request_cache.reset(token)
        self.assertTrue(all(look["frame"] == "pfx-frame--doppler" for look in looks))
