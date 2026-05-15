from decimal import Decimal
from io import BytesIO
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.profiles.services.ledger import LedgerError, LedgerIdempotencyConflictError, apply_ledger_delta
from modules.v2.profiles.services.faceit import build_faceit_level_image_url, sync_faceit_profile
from modules.v2.profiles.forms import AdminProfileIdentityForm
from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.store.models import ThemeStoreListing, UserThemePurchase
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.tg_notifier.models import TelegramNotifierConfig
from modules.v2.system.services import update_module_states
from modules.v2.profiles.presentation import get_profile_avatar_url
from modules.v2.profiles.services.steam_profile import (
    extract_background_url,
    extract_background_video_url,
    fetch_steam_level,
    sync_profile_background_if_needed,
)
from PIL import Image

User = get_user_model()


def _build_test_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(40, 72, 132)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_1X1_BYTES = _build_test_png_bytes()


class ProfileTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_profile_created_on_user_create(self):
        user = User.objects.create_user(username="user1", password="StrongPass123")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_profile_requires_login(self):
        response = self.client.get(reverse("profiles:profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_available_for_authenticated_user(self):
        user = User.objects.create_user(username="user2", password="StrongPass123")
        self.client.login(username="user2", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FACEIT")


    def test_profile_detail_view_forbidden_for_foreign_regular_user(self):
        viewer = User.objects.create_user(username="viewer1", password="StrongPass123")
        target = User.objects.create_user(username="target10", password="StrongPass123", email="target@example.com")
        target_profile = UserProfile.objects.get(user=target)
        target_profile.balance_coins = Decimal("125.00")
        target_profile.telegram_chat_id = "7151390596"
        target_profile.save(update_fields=["balance_coins", "telegram_chat_id"])
        adjust_balance(
            user=target,
            delta=Decimal("25.00"),
            reason="admin_adjust",
            idempotency_key="profile-detail-private-1",
        )

        self.client.login(username="viewer1", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_detail", args=[target.id]))

        self.assertEqual(response.status_code, 403)

    def test_profile_detail_view_shows_private_data_for_admin(self):
        admin = User.objects.create_user(username="admin_profile", password="StrongPass123")
        admin_profile = UserProfile.objects.get(user=admin)
        admin_profile.role = UserRole.ADMIN
        admin_profile.save(update_fields=["role"])

        target = User.objects.create_user(username="target11", password="StrongPass123", email="target11@example.com")
        target_profile = UserProfile.objects.get(user=target)
        target_profile.balance_coins = Decimal("90.00")
        target_profile.telegram_chat_id = "7151390596"
        target_profile.save(update_fields=["balance_coins", "telegram_chat_id"])
        adjust_balance(
            user=target,
            delta=Decimal("10.00"),
            reason="admin_adjust",
            idempotency_key="profile-detail-admin-1",
        )

        self.client.login(username="admin_profile", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_detail", args=[target.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "target11@example.com")
        self.assertContains(response, "7151390596")
        self.assertContains(response, 'recent-operations')

    def test_profile_detail_view_shows_private_data_for_moderator(self):
        moderator = User.objects.create_user(username="mod_profile", password="StrongPass123")
        mod_profile = UserProfile.objects.get(user=moderator)
        mod_profile.role = UserRole.MOD
        mod_profile.save(update_fields=["role"])

        target = User.objects.create_user(username="target_mod_view", password="StrongPass123", email="target_mod_view@example.com")
        target_profile = UserProfile.objects.get(user=target)
        target_profile.balance_coins = Decimal("75.00")
        target_profile.telegram_chat_id = "7151390596"
        target_profile.save(update_fields=["balance_coins", "telegram_chat_id"])

        self.client.login(username="mod_profile", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_detail", args=[target.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "target_mod_view@example.com")
        self.assertContains(response, "7151390596")

    def test_profile_detail_view_uses_target_subscription_context(self):
        admin = User.objects.create_user(username="admin_sub_view", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])

        target = User.objects.create_user(username="target_subscribed", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=4)
        UserSubscription.objects.create(user=target, plan=plan)

        self.client.login(username="admin_sub_view", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_detail", args=[target.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, plan.name)
        self.assertContains(response, "Subscription")

    def test_admin_can_update_profile_balance_username_and_subscription(self):
        admin = User.objects.create_user(username="admin_manage_profile", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])

        target = User.objects.create_user(username="target_manage_profile", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=5)

        self.client.login(username="admin_manage_profile", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "admin_section": "identity",
                "username": "target_manage_profile_updated",
                "balance_coins": "150.00",
                "role": UserRole.FARMER,
            },
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "admin_section": "site_subscription",
                "site_subscription_plan": str(plan.id),
                "site_subscription_expires_at": "",
                "site_subscription_notes": "manual issue",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        target.profile_v2.refresh_from_db()
        self.assertEqual(target.username, "target_manage_profile_updated")
        self.assertEqual(target.profile_v2.balance_coins, Decimal("150.00"))
        self.assertEqual(target.profile_v2.role, UserRole.FARMER)
        self.assertContains(response, "Site-подписка обновлена.")

        active_subscription = UserSubscription.objects.get(user=target, status=UserSubscription.Status.ACTIVE)
        self.assertEqual(active_subscription.plan_id, plan.id)
        self.assertEqual(active_subscription.notes, "manual issue")

    def test_regular_user_cannot_post_profile_admin_update(self):
        viewer = User.objects.create_user(username="profile_regular_viewer", password="StrongPass123")
        target = User.objects.create_user(username="profile_regular_target", password="StrongPass123")

        self.client.login(username="profile_regular_viewer", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "username": "profile_regular_target",
                "balance_coins": "0.00",
                "subscription_plan": "",
                "subscription_expires_at": "",
                "subscription_notes": "",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_assign_role_higher_than_self(self):
        admin = User.objects.create_user(username="profile_admin_role_guard", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])
        target = User.objects.create_user(username="profile_target_role_guard", password="StrongPass123")

        self.client.login(username="profile_admin_role_guard", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "admin_section": "identity",
                "username": "profile_target_role_guard",
                "balance_coins": "0.00",
                "role": UserRole.SUPER_ADMIN,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target.profile_v2.refresh_from_db()
        self.assertEqual(target.profile_v2.role, UserRole.USER)

    def test_admin_can_update_role_and_sync_staff_flags(self):
        admin = User.objects.create_user(username="profile_role_admin", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])
        target = User.objects.create_user(username="profile_role_target", password="StrongPass123")

        self.client.login(username="profile_role_admin", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "admin_section": "identity",
                "username": "profile_role_target",
                "balance_coins": "0.00",
                "role": UserRole.MOD,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        target.profile_v2.refresh_from_db()
        self.assertEqual(target.profile_v2.role, UserRole.MOD)
        self.assertTrue(target.is_staff)
        self.assertFalse(target.is_superuser)

    def test_admin_identity_form_filters_role_choices_by_actor_rank(self):
        actor = User.objects.create_user(username="profile_role_choice_actor", password="StrongPass123")
        actor.profile_v2.role = UserRole.ADMIN
        actor.profile_v2.save(update_fields=["role"])
        target = User.objects.create_user(username="profile_role_choice_target", password="StrongPass123")

        form = AdminProfileIdentityForm(
            target_user=target,
            target_profile=target.profile_v2,
            actor_user=actor,
        )
        available_roles = [value for value, _label in form.fields["role"].choices]

        self.assertIn(UserRole.ADMIN, available_roles)
        self.assertNotIn(UserRole.CHIEF_ADMIN, available_roles)
        self.assertNotIn(UserRole.SUPER_ADMIN, available_roles)

    def test_admin_identity_form_hides_role_choices_without_actor(self):
        target = User.objects.create_user(username="profile_role_choice_no_actor", password="StrongPass123")

        form = AdminProfileIdentityForm(
            target_user=target,
            target_profile=target.profile_v2,
            actor_user=None,
        )

        self.assertEqual(list(form.fields["role"].choices), [])

    def test_profile_theme_preferences_show_only_purchased_or_public_themes(self):
        update_module_states({"store": True})
        user = User.objects.create_user(username="theme_profile_user", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=1)
        UserSubscription.objects.create(user=user, plan=plan)
        listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")
        self.client.login(username="theme_profile_user", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, listing.title)

        UserThemePurchase.objects.create(
            user=user,
            listing=listing,
            theme_key=listing.theme_key,
            amount_rub=listing.price_rub,
        )
        response = self.client.get(reverse("profiles:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, listing.title)

    def test_profile_theme_preferences_update_persists_scope_selection(self):
        update_module_states({"store": True})
        user = User.objects.create_user(username="theme_profile_save_user", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=1)
        UserSubscription.objects.create(user=user, plan=plan)
        listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")
        UserThemePurchase.objects.create(
            user=user,
            listing=listing,
            theme_key=listing.theme_key,
            amount_rub=listing.price_rub,
        )
        self.client.login(username="theme_profile_save_user", password="StrongPass123")

        response = self.client.post(
            reverse("profiles:profile_theme_preferences_update"),
            {"theme_scope__steam_catalog": listing.theme_key},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user.profile_v2.refresh_from_db()
        self.assertEqual(user.profile_v2.theme_preferences.get("steam_catalog"), listing.theme_key)

    def test_adjust_balance_topup(self):
        user = User.objects.create_user(username="user3", password="StrongPass123")

        adjust_balance(
            user=user,
            delta=Decimal("100.00"),
            reason="topup",
            idempotency_key="topup-1",
        )

        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.balance_coins, Decimal("100.00"))

    def test_adjust_balance_cannot_go_negative(self):
        user = User.objects.create_user(username="user4", password="StrongPass123")

        with self.assertRaises(BalanceError):
            adjust_balance(
                user=user,
                delta=Decimal("-10.00"),
                reason="case_open",
                idempotency_key="case-open-1",
            )

    def test_extract_background_url_from_inline_style(self):
        html = """
        <div class="profile_page_background"
             style="background-image: url('https://cdn.akamai.steamstatic.com/steamcommunity/public/images/items/bg.jpg');">
        </div>
        """
        self.assertEqual(
            extract_background_url(html),
            "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/items/bg.jpg",
        )

    def test_extract_background_url_from_escaped_json_value(self):
        html = r'''
        <script>
            const profileTheme = {
                "profile_background":"https:\/\/shared.fastly.steamstatic.com\/store_item_assets\/steam\/communityitems\/123456\/abcdef/profile_background.jpg"
            };
        </script>
        '''
        self.assertEqual(
            extract_background_url(html),
            "https://shared.fastly.steamstatic.com/store_item_assets/steam/communityitems/123456/abcdef/profile_background.jpg",
        )

    def test_extract_background_video_url_from_profile_json(self):
        html = r'''
        <script>
            const profileTheme = {
                "profile_background":"https:\/\/shared.fastly.steamstatic.com\/store_item_assets\/steam\/communityitems\/123456\/abcdef/profile_background.jpg",
                "profile_background_video":"https:\/\/shared.fastly.steamstatic.com\/store_item_assets\/steam\/communityitems\/123456\/abcdef\/profile_background.webm"
            };
        </script>
        '''
        self.assertEqual(
            extract_background_video_url(html),
            "https://shared.fastly.steamstatic.com/store_item_assets/steam/communityitems/123456/abcdef/profile_background.webm",
        )

    def test_extract_background_url_ignores_showcase_game_image(self):
        html = r'''
        <div class="favorite_game_showcase">
            <img src="https://cdn.akamai.steamstatic.com/steam/apps/730/header.jpg">
        </div>
        <script>
            const profileTheme = {
                "profile_background":"https:\/\/shared.fastly.steamstatic.com\/store_item_assets\/steam\/communityitems\/999999\/room_scene/profile_background.jpg"
            };
        </script>
        '''
        self.assertEqual(
            extract_background_url(html),
            "https://shared.fastly.steamstatic.com/store_item_assets/steam/communityitems/999999/room_scene/profile_background.jpg",
        )

    def test_extract_background_url_without_profile_context_returns_empty(self):
        html = """
        <div class="favorite_game_showcase">
            <img src="https://cdn.akamai.steamstatic.com/steam/apps/730/header.jpg">
        </div>
        """
        self.assertEqual(extract_background_url(html), "")

    @patch("modules.v2.profiles.services.faceit.urlopen")
    @patch("modules.v2.profiles.services.faceit.settings")
    def test_sync_faceit_profile_success(self, settings_mock, urlopen_mock):
        settings_mock.FACEIT_API_KEY = "test-key"

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'''
                {
                    "player_id": "faceit-player-1",
                    "nickname": "NaRkOzZ",
                    "avatar": "https://faceit.example/avatar.png",
                    "games": {
                        "cs2": {
                            "skill_level": 8,
                            "faceit_elo": 1789
                        }
                    }
                }
                '''

        urlopen_mock.return_value = DummyResponse()

        user = User.objects.create_user(username="user5", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.steam_id = "76561199647175346"
        profile.save(update_fields=["steam_id"])

        result = sync_faceit_profile(profile)

        self.assertTrue(result.ok)
        profile.refresh_from_db()
        self.assertEqual(profile.faceit_player_id, "faceit-player-1")
        self.assertEqual(profile.faceit_nickname, "NaRkOzZ")
        self.assertEqual(profile.faceit_skill_level, 8)
        self.assertEqual(profile.faceit_elo, 1789)
        self.assertEqual(profile.faceit_level_image_url, build_faceit_level_image_url(8))

    @patch("modules.v2.profiles.services.faceit.settings")
    def test_sync_faceit_profile_without_api_key_sets_error(self, settings_mock):
        settings_mock.FACEIT_API_KEY = ""

        user = User.objects.create_user(username="user6", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.steam_id = "76561199647175346"
        profile.save(update_fields=["steam_id"])

        result = sync_faceit_profile(profile)

        self.assertFalse(result.ok)
        profile.refresh_from_db()
        self.assertIn("FACEIT_API_KEY", profile.faceit_last_error)

    @patch("modules.v2.profiles.services.steam_profile.settings")
    @patch("modules.v2.profiles.services.steam_profile.urllib.request.urlopen")
    def test_fetch_steam_level_success(self, urlopen_mock, settings_mock):
        settings_mock.SOCIAL_AUTH_STEAM_API_KEY = "steam-key"

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"response":{"player_level":50}}'

        urlopen_mock.return_value = DummyResponse()

        level = fetch_steam_level("76561199647175346")
        self.assertEqual(level, 50)

    @patch("modules.v2.profiles.views.sync_profile_steam_meta_if_needed")
    @patch("modules.v2.profiles.views.sync_profile_from_steam_api")
    def test_sync_steam_view_updates_identity_background_and_level(self, sync_identity_mock, sync_meta_mock):
        user = User.objects.create_user(username="user7", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.steam_id = "76561199647175346"
        profile.steam_profile_url = "https://steamcommunity.com/id/testuser/"
        profile.save(update_fields=["steam_id", "steam_profile_url"])

        def identity_side_effect(profile, save=True):
            profile.steam_personaname = "UpdatedNick"
            profile.steam_avatar = "https://cdn.example/avatar-new.jpg"
            profile.save(update_fields=["steam_personaname", "steam_avatar"])
            return profile

        def meta_side_effect(profile, force=False):
            profile.steam_profile_background = "https://cdn.akamai.steamstatic.com/bg.jpg"
            profile.steam_level = 50
            profile.save(update_fields=["steam_profile_background", "steam_level"])
            return profile

        sync_identity_mock.side_effect = identity_side_effect
        sync_meta_mock.side_effect = meta_side_effect

        self.client.login(username="user7", password="StrongPass123")
        response = self.client.post(reverse("profiles:sync_steam"))

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.steam_personaname, "UpdatedNick")
        self.assertEqual(profile.steam_avatar, "https://cdn.example/avatar-new.jpg")
        self.assertEqual(profile.steam_profile_background, "https://cdn.akamai.steamstatic.com/bg.jpg")
        self.assertEqual(profile.steam_level, 50)

    def test_sync_steam_view_requires_steam_id(self):
        user = User.objects.create_user(username="user8", password="StrongPass123")
        self.client.login(username="user8", password="StrongPass123")

        response = self.client.post(reverse("profiles:sync_steam"))
        self.assertEqual(response.status_code, 302)

    def test_extract_background_video_url_from_profile_animated_background_sources(self):
        html = """
        <div class="profile_animated_background">
            <video loop poster="https://shared.akamai.steamstatic.com/xxx/profile_background.jpg">
                <source src="https://shared.akamai.steamstatic.com/xxx/profile_background.webm" type="video/webm">
                <source src="https://shared.akamai.steamstatic.com/xxx/profile_background.mp4" type="video/mp4">
            </video>
        </div>
        """
        self.assertEqual(
            extract_background_video_url(html),
            "https://shared.akamai.steamstatic.com/xxx/profile_background.webm",
        )
    
    @patch("modules.v2.profiles.services.steam_profile.fetch_steam_profile_media")
    def test_sync_profile_background_keeps_existing_video_if_parser_returns_empty(self, media_mock):
        media_mock.return_value = ("https://cdn.example/bg.jpg", "")

        user = User.objects.create_user(username="user9", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.steam_profile_url = "https://steamcommunity.com/id/testuser/"
        profile.steam_profile_background_video = "https://cdn.example/existing.webm"
        profile.save(update_fields=["steam_profile_url", "steam_profile_background_video"])

        sync_profile_background_if_needed(profile, force=True)

        profile.refresh_from_db()
        self.assertEqual(profile.steam_profile_background_video, "https://cdn.example/existing.webm")
        self.assertEqual(profile.steam_profile_background, "https://cdn.example/bg.jpg")
    def test_apply_ledger_delta_is_idempotent(self):
        user = User.objects.create_user(username="ledger_user", password="StrongPass123")

        first = apply_ledger_delta(
            user=user,
            delta=Decimal("50.00"),
            reason="admin_adjust",
            idempotency_key="ledger-topup-1",
        )
        second = apply_ledger_delta(
            user=user,
            delta=Decimal("50.00"),
            reason="admin_adjust",
            idempotency_key="ledger-topup-1",
        )

        user.profile_v2.refresh_from_db()
        self.assertEqual(first.entry.id, second.entry.id)
        self.assertEqual(user.profile_v2.balance_coins, Decimal("50.00"))

    def test_apply_ledger_delta_same_key_for_different_user_creates_separate_entry(self):
        first_user = User.objects.create_user(username="ledger_scope_user_1", password="StrongPass123")
        second_user = User.objects.create_user(username="ledger_scope_user_2", password="StrongPass123")

        first_entry = apply_ledger_delta(
            user=first_user,
            delta=Decimal("10.00"),
            reason="topup",
            amount_snapshot=Decimal("10.00"),
            idempotency_key="shared-scope-key",
        )
        second_entry = apply_ledger_delta(
            user=second_user,
            delta=Decimal("15.00"),
            reason="topup",
            amount_snapshot=Decimal("15.00"),
            idempotency_key="shared-scope-key",
        )

        first_user.profile_v2.refresh_from_db()
        second_user.profile_v2.refresh_from_db()
        self.assertNotEqual(first_entry.entry.id, second_entry.entry.id)
        self.assertEqual(first_user.profile_v2.balance_coins, Decimal("10.00"))
        self.assertEqual(second_user.profile_v2.balance_coins, Decimal("15.00"))

    def test_apply_ledger_delta_rejects_conflicting_reuse_of_same_idempotency_key(self):
        user = User.objects.create_user(username="ledger_conflict_user", password="StrongPass123")
        apply_ledger_delta(
            user=user,
            delta=Decimal("20.00"),
            reason="topup",
            amount_snapshot=Decimal("20.00"),
            idempotency_key="ledger-conflict-key",
        )

        with self.assertRaises(LedgerIdempotencyConflictError):
            apply_ledger_delta(
                user=user,
                delta=Decimal("25.00"),
                reason="topup",
                amount_snapshot=Decimal("25.00"),
                idempotency_key="ledger-conflict-key",
            )

        user.profile_v2.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, Decimal("20.00"))
        self.assertEqual(user.ledger_entries.filter(idempotency_key="ledger-conflict-key").count(), 1)

    def test_apply_ledger_delta_cannot_go_negative(self):
        user = User.objects.create_user(username="ledger_user_negative", password="StrongPass123")

        with self.assertRaises(LedgerError):
            apply_ledger_delta(
                user=user,
                delta=Decimal("-1.00"),
                reason="admin_adjust",
            )

    def test_profile_page_shows_connect_telegram_button_for_owner(self):
        user = User.objects.create_user(username="tg_owner", password="StrongPass123")
        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template="Предмет: {{ item_name }}",
        )

        self.client.login(username="tg_owner", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Подключить Telegram")

    def test_profile_telegram_connect_creates_link_token(self):
        user = User.objects.create_user(username="tg_connect", password="StrongPass123")
        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="123456:ABCDEF",
            bot_username="caseup_bot",
            default_chat_id="-100555",
            investment_message_template="Предмет: {{ item_name }}",
        )

        self.client.login(username="tg_connect", password="StrongPass123")
        response = self.client.post(reverse("profiles:telegram_connect"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ссылка для подключения Telegram подготовлена.")
        self.assertContains(response, "https://t.me/caseup_bot?start=caseup_")

    def test_profile_telegram_disconnect_clears_link(self):
        user = User.objects.create_user(username="tg_disconnect", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.telegram_chat_id = "7151390596"
        profile.telegram_username = "narkozzplay"
        profile.telegram_first_name = "Fedor"
        profile.save()

        self.client.login(username="tg_disconnect", password="StrongPass123")
        response = self.client.post(reverse("profiles:telegram_disconnect"), follow=True)

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertIsNone(profile.telegram_chat_id)
        self.assertContains(response, "Telegram отключен от вашего профиля.")


class ProfileAvatarPatchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="avatar_patch_user", password="StrongPass123")
        self.media_root = tempfile.mkdtemp(prefix="caseup-avatar-tests-")
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

    def test_profile_page_uses_no_image_placeholder_when_avatar_missing(self):
        self.client.login(username="avatar_patch_user", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "img/placeholders/no_image.svg")

    def test_profile_page_contains_custom_avatar_upload_ui(self):
        self.client.login(username="avatar_patch_user", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-avatar-upload")
        self.assertContains(response, "avatar-upload__button")
        self.assertContains(response, "data-avatar-upload-file-name")
        self.assertContains(response, "data-avatar-upload-preview-wrap")
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_profile_avatar_upload_view_saves_uploaded_avatar(self):
        self.client.login(username="avatar_patch_user", password="StrongPass123")
        uploaded_file = SimpleUploadedFile(
            "avatar.png",
            PNG_1X1_BYTES,
            content_type="image/png",
        )

        with self.settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                reverse("profiles:profile_avatar_upload"),
                {"uploaded_avatar": uploaded_file},
                follow=True,
            )

            self.assertEqual(response.status_code, 200)
            self.user.profile_v2.refresh_from_db()
            self.assertTrue(bool(self.user.profile_v2.uploaded_avatar))
            self.assertIn("profiles/avatars/", self.user.profile_v2.uploaded_avatar.name)
            self.assertContains(response, "Аватар обновлён.")

    def test_profile_avatar_upload_invalid_file_shows_error_and_does_not_save(self):
        self.client.login(username="avatar_patch_user", password="StrongPass123")
        uploaded_file = SimpleUploadedFile(
            "avatar.txt",
            b"not an image",
            content_type="text/plain",
        )

        with self.settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                reverse("profiles:profile_avatar_upload"),
                {"uploaded_avatar": uploaded_file},
            )

            self.assertEqual(response.status_code, 200)
            self.user.profile_v2.refresh_from_db()
            self.assertFalse(bool(self.user.profile_v2.uploaded_avatar))
            self.assertContains(response, "Не удалось обновить аватар.")
            self.assertContains(response, "Загрузите правильное изображение")

    def test_avatar_source_priority_uploaded_then_steam_then_default(self):
        profile = self.user.profile_v2
        profile.uploaded_avatar = None
        profile.steam_avatar = ""
        profile.save(update_fields=["uploaded_avatar", "steam_avatar"])
        default_avatar = get_profile_avatar_url(profile)
        self.assertTrue(default_avatar.endswith("img/placeholders/no_image.svg"))

        profile.steam_avatar = "https://cdn.example.com/steam-avatar.jpg"
        profile.save(update_fields=["steam_avatar"])
        steam_avatar = get_profile_avatar_url(profile)
        self.assertEqual(steam_avatar, "https://cdn.example.com/steam-avatar.jpg")

        with self.settings(MEDIA_ROOT=self.media_root):
            profile.uploaded_avatar.save(
                "local-avatar.png",
                SimpleUploadedFile("local-avatar.png", PNG_1X1_BYTES, content_type="image/png"),
                save=True,
            )
            uploaded_avatar_url = get_profile_avatar_url(profile)

        self.assertIn("/profiles/avatars/", uploaded_avatar_url)


class ProfileBalanceConstraintTests(TransactionTestCase):
    reset_sequences = True

    def test_balance_db_constraint_rejects_negative_value(self):
        user = User.objects.create_user(username="db_balance_user", password="StrongPass123")
        profile = user.profile_v2

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                profile.balance_coins = Decimal("-0.01")
                profile.save(update_fields=["balance_coins", "updated_at"])

    def test_adjust_balance_uses_shared_ledger_flow(self):
        user = User.objects.create_user(username="shared_ledger_user", password="StrongPass123")

        entry = adjust_balance(
            user=user,
            delta=Decimal("25.00"),
            reason="admin_adjust",
            idempotency_key="shared-ledger-topup-1",
        )

        user.profile_v2.refresh_from_db()
        self.assertEqual(entry.balance_after, Decimal("25.00"))
        self.assertEqual(user.profile_v2.balance_coins, Decimal("25.00"))
