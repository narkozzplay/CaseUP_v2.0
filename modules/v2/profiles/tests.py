from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.middleware import UserLastSeenMiddleware
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.profiles.services.ledger import LedgerError, LedgerIdempotencyConflictError, apply_ledger_delta
from modules.v2.profiles.services.faceit import build_faceit_level_image_url, sync_faceit_profile
from modules.v2.profiles.services.presence import is_profile_online
from modules.v2.profiles.forms import AdminProfileIdentityForm
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
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
from modules.v2.social.models import SocialMembership, SocialSpace
from modules.v2.social.services import build_space_url
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


class ProfilePresenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _response(self, _request):
        return HttpResponse("ok")

    def test_is_profile_online_with_fresh_last_seen(self):
        user = User.objects.create_user(username="presence_fresh", password="StrongPass123")
        profile = user.profile_v2
        now = timezone.now()
        profile.last_seen_at = now - timedelta(minutes=1)

        self.assertTrue(is_profile_online(profile, now=now))

    def test_is_profile_online_false_without_last_seen(self):
        user = User.objects.create_user(username="presence_empty", password="StrongPass123")
        profile = user.profile_v2
        profile.last_seen_at = None

        self.assertFalse(is_profile_online(profile, now=timezone.now()))

    def test_is_profile_online_false_when_expired(self):
        user = User.objects.create_user(username="presence_expired", password="StrongPass123")
        profile = user.profile_v2
        now = timezone.now()
        profile.last_seen_at = now - timedelta(minutes=6)

        self.assertFalse(is_profile_online(profile, now=now))

    def test_last_seen_middleware_ignores_anonymous_user(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        middleware = UserLastSeenMiddleware(self._response)

        response = middleware(request)

        self.assertEqual(response.status_code, 200)

    def test_last_seen_middleware_updates_authenticated_user(self):
        user = User.objects.create_user(username="presence_touch", password="StrongPass123")
        profile = user.profile_v2
        profile.last_seen_at = None
        profile.save(update_fields=["last_seen_at"])
        request = self.factory.get("/")
        request.user = user
        middleware = UserLastSeenMiddleware(self._response)

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.last_seen_at)

    def test_last_seen_middleware_throttles_updates(self):
        user = User.objects.create_user(username="presence_throttle", password="StrongPass123")
        profile = user.profile_v2
        first_now = timezone.now()
        second_now = first_now + timedelta(seconds=30)
        middleware = UserLastSeenMiddleware(self._response)

        with patch("modules.v2.profiles.services.presence.timezone.now", return_value=first_now):
            first_request = self.factory.get("/")
            first_request.user = user
            middleware(first_request)

        with patch("modules.v2.profiles.services.presence.timezone.now", return_value=second_now):
            second_request = self.factory.get("/")
            second_request.user = user
            middleware(second_request)

        profile.refresh_from_db()
        self.assertEqual(profile.last_seen_at, first_now)

    def test_last_seen_middleware_does_not_create_missing_profile(self):
        user = User.objects.create_user(username="presence_missing_profile", password="StrongPass123")
        UserProfile.objects.filter(user=user).delete()
        request = self.factory.get("/")
        request.user = user
        middleware = UserLastSeenMiddleware(self._response)

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserProfile.objects.filter(user=user).exists())


class UsersCatalogTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _make_user(self, username: str, role: str = UserRole.USER, steam_name: str = "", steam_id: str = ""):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = role
        profile.steam_personaname = steam_name
        update_fields = ["role", "steam_personaname"]
        if steam_id:
            profile.steam_id = steam_id
            update_fields.append("steam_id")
        profile.save(update_fields=update_fields)
        return user

    def _make_admin(self, username: str = "users_catalog_admin"):
        return self._make_user(username, role=UserRole.ADMIN, steam_name="Catalog Admin")

    def test_users_catalog_route_exists(self):
        self.assertEqual(reverse("profiles:users_catalog"), "/users/")

    def test_users_catalog_requires_login(self):
        response = self.client.get(reverse("profiles:users_catalog"))
        self.assertEqual(response.status_code, 302)

    def test_users_catalog_forbidden_for_regular_user(self):
        user = self._make_user("users_catalog_plain")
        self.client.login(username=user.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"))

        self.assertEqual(response.status_code, 403)

    def test_users_catalog_available_for_admin(self):
        admin = self._make_admin()
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Users Catalog")
        self.assertContains(response, "Пользователи")
        self.assertContains(response, reverse("profiles:users_catalog"))

    def test_users_catalog_search_by_steam_nickname(self):
        admin = self._make_admin("users_catalog_search_admin")
        target = self._make_user("internal_search_user", steam_name="Steam Search Nick")
        other = self._make_user("other_search_user", steam_name="Other Nick")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"q": "Steam Search"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Steam Search Nick")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertNotContains(response, "Other Nick")
        self.assertNotContains(response, reverse("profiles:profile_detail", args=[other.id]))

    def test_users_catalog_search_by_username(self):
        admin = self._make_admin("users_catalog_username_admin")
        target = self._make_user("internal_username_match", steam_name="Visible Username Match")
        other = self._make_user("unrelated_username", steam_name="Unrelated User")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"q": "internal_username_match"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible Username Match")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertNotContains(response, "Unrelated User")
        self.assertNotContains(response, reverse("profiles:profile_detail", args=[other.id]))

    def test_users_catalog_search_by_user_id(self):
        admin = self._make_admin("users_catalog_id_admin")
        target = self._make_user("id_search_target", steam_name="ID Search Target")
        other = self._make_user("id_search_other", steam_name="ID Search Other")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"q": str(target.id)})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ID Search Target")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertNotContains(response, "ID Search Other")
        self.assertNotContains(response, reverse("profiles:profile_detail", args=[other.id]))

    def test_users_catalog_filter_by_role(self):
        admin = self._make_admin("users_catalog_role_admin")
        farmer = self._make_user("catalog_farmer", role=UserRole.FARMER, steam_name="Farmer Visible")
        plain = self._make_user("catalog_plain", role=UserRole.USER, steam_name="Plain Visible")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"role": UserRole.FARMER})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Farmer Visible")
        self.assertContains(response, reverse("profiles:profile_detail", args=[farmer.id]))
        self.assertNotContains(response, "Plain Visible")
        self.assertNotContains(response, reverse("profiles:profile_detail", args=[plain.id]))

    def test_users_catalog_grid_mode(self):
        admin = self._make_admin("users_catalog_grid_admin")
        target = self._make_user("users_catalog_grid_target", steam_name="Grid Target")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"view": "grid", "q": "Grid Target"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-users-catalog-view="grid"')
        self.assertContains(response, "users-grid")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertContains(response, "presence-indicator")

    def test_users_catalog_list_mode(self):
        admin = self._make_admin("users_catalog_list_admin")
        target = self._make_user("users_catalog_list_target", steam_name="List Target")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"view": "list", "q": "List Target"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-users-catalog-view="list"')
        self.assertContains(response, "users-list__row")
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertContains(response, "presence-indicator")

    def test_users_catalog_unknown_view_falls_back_to_grid(self):
        admin = self._make_admin("users_catalog_unknown_view_admin")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"view": "cards"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-users-catalog-view="grid"')
        self.assertContains(response, "users-grid")

    def test_users_catalog_pagination(self):
        admin = self._make_admin("users_catalog_page_admin")
        for index in range(25):
            self._make_user(f"page_user_{index:02d}")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Страница 2 /")
        self.assertContains(response, "page_user_00")

    def test_users_catalog_pagination_preserves_query_params(self):
        admin = self._make_admin("users_catalog_query_admin")
        for index in range(25):
            self._make_user(f"catalog_keep_{index:02d}", role=UserRole.USER)
        self.client.login(username=admin.username, password="StrongPass123")

        url = f"{reverse('profiles:users_catalog')}?q=catalog_keep&role=user&sort=role&view=list"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "page=2&amp;q=catalog_keep&amp;role=user&amp;sort=role&amp;view=list",
            html=False,
        )

    def test_users_catalog_card_contains_profile_link(self):
        admin = self._make_admin("users_catalog_link_admin")
        target = self._make_user("catalog_link_target", steam_name="Link Target")
        self.client.login(username=admin.username, password="StrongPass123")

        response = self.client.get(reverse("profiles:users_catalog"), {"q": "Link Target"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("profiles:profile_detail", args=[target.id]))
        self.assertContains(response, reverse("profiles:profile_start_direct_chat", args=[target.id]))


    def test_profile_detail_view_public_for_foreign_regular_user_hides_private_data(self):
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

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "profiles/partials/profile_content.html")
        self.assertFalse(response.context["can_view_private_profile"])
        self.assertEqual(response.context["recent_entries"], [])
        self.assertNotContains(response, "7151390596")
        self.assertNotContains(response, 'id="recent-operations"')

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
        # Email removed from profile page UI (moved to settings)
        self.assertContains(response, "target11")  # username still shown
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
        # Email removed from profile page UI (moved to settings page)
        self.assertContains(response, "target_mod_view")  # username still shown
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
                "role": UserRole.FARMER,
            },
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[target.id]),
            {
                "admin_section": "balance",
                "balance_operation": "set",
                "balance_amount": "150.00",
                "balance_comment": "test credit",
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

    def _make_balance_admin(self, username="balance_admin"):
        admin = User.objects.create_user(username=username, password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])
        self.client.login(username=username, password="StrongPass123")
        return admin

    def _post_balance(self, target, **data):
        payload = {"admin_section": "balance", **data}
        return self.client.post(reverse("profiles:profile_admin_update", args=[target.id]), payload)

    def test_admin_balance_add_subtract_and_set_are_logged_in_ledger(self):
        admin = self._make_balance_admin()
        target = User.objects.create_user(username="balance_target", password="StrongPass123")

        response = self._post_balance(target, balance_operation="add", balance_amount="100", balance_comment="бонус")
        self.assertRedirects(
            response,
            reverse("profiles:profile_user_settings", args=[target.id]) + "#settings-admin-balance",
            fetch_redirect_response=False,
        )
        self._post_balance(target, balance_operation="subtract", balance_amount="30", balance_comment="штраф")
        self._post_balance(target, balance_operation="set", balance_amount="500", balance_comment="ручная правка")

        target.profile_v2.refresh_from_db()
        self.assertEqual(target.profile_v2.balance_coins, Decimal("500.00"))
        entries = list(LedgerEntry.objects.filter(user=target).order_by("id"))
        self.assertEqual([entry.delta for entry in entries], [Decimal("100.00"), Decimal("-30.00"), Decimal("430.00")])
        self.assertEqual([entry.comment for entry in entries], ["бонус", "штраф", "ручная правка"])
        self.assertTrue(all(entry.created_by_id == admin.id for entry in entries))
        self.assertTrue(all(entry.reason == LedgerReason.ADMIN_ADJUST for entry in entries))

    def test_admin_balance_rejects_overdraft_and_missing_comment(self):
        self._make_balance_admin("balance_admin_guard")
        target = User.objects.create_user(username="balance_target_guard", password="StrongPass123")

        response = self._post_balance(target, balance_operation="subtract", balance_amount="10", balance_comment="x")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нельзя списать больше текущего баланса")

        response = self._post_balance(target, balance_operation="add", balance_amount="10", balance_comment="  ")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LedgerEntry.objects.filter(user=target).exists())

    def test_regular_user_cannot_change_balance(self):
        viewer = User.objects.create_user(username="balance_regular", password="StrongPass123")
        self.client.login(username="balance_regular", password="StrongPass123")

        response = self._post_balance(viewer, balance_operation="add", balance_amount="1000", balance_comment="free money")

        self.assertEqual(response.status_code, 403)
        viewer.profile_v2.refresh_from_db()
        self.assertEqual(viewer.profile_v2.balance_coins, Decimal("0.00"))

    def test_admin_settings_page_shows_change_balance_card(self):
        self._make_balance_admin("balance_admin_page")
        target = User.objects.create_user(username="balance_target_page", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile_user_settings", args=[target.id]))

        self.assertContains(response, "Изменить баланс")
        self.assertContains(response, 'name="balance_comment"')

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

        self.assertIn(UserRole.MOD, available_roles)
        self.assertNotIn(UserRole.ADMIN, available_roles)
        self.assertNotIn(UserRole.CHIEF_ADMIN, available_roles)
        self.assertNotIn(UserRole.SUPER_ADMIN, available_roles)

    def _make_role_user(self, username, role, *, superuser=False):
        if superuser:
            user = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="StrongPass123")
        else:
            user = User.objects.create_user(username=username, password="StrongPass123")
        user.profile_v2.role = role
        user.profile_v2.save(update_fields=["role"])
        return user

    def test_admin_cannot_demote_super_admin(self):
        self._make_role_user("profile_demote_admin", UserRole.ADMIN)
        boss = self._make_role_user("profile_demote_boss", UserRole.SUPER_ADMIN, superuser=True)

        self.client.login(username="profile_demote_admin", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[boss.id]),
            {"admin_section": "identity", "username": "profile_demote_boss", "role": UserRole.USER},
        )

        self.assertEqual(response.status_code, 403)
        boss.refresh_from_db()
        self.assertTrue(boss.is_superuser)
        self.assertEqual(boss.profile_v2.role, UserRole.SUPER_ADMIN)

    def test_admin_cannot_edit_peer_admin_balance(self):
        self._make_role_user("profile_peer_admin_actor", UserRole.ADMIN)
        peer = self._make_role_user("profile_peer_admin_target", UserRole.ADMIN)

        self.client.login(username="profile_peer_admin_actor", password="StrongPass123")
        response = self._post_balance(peer, balance_operation="add", balance_amount="100", balance_comment="peer")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LedgerEntry.objects.filter(user=peer).exists())

    def test_admin_cannot_adjust_own_balance(self):
        admin = self._make_balance_admin("profile_self_balance_admin")

        response = self._post_balance(admin, balance_operation="add", balance_amount="1000", balance_comment="self")

        self.assertEqual(response.status_code, 403)
        admin.profile_v2.refresh_from_db()
        self.assertEqual(admin.profile_v2.balance_coins, Decimal("0.00"))

    def test_settings_page_hides_admin_cards_for_higher_ranked_target(self):
        self._make_role_user("profile_cards_admin", UserRole.ADMIN)
        chief = self._make_role_user("profile_cards_chief", UserRole.CHIEF_ADMIN)

        self.client.login(username="profile_cards_admin", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_user_settings", args=[chief.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="balance_comment"')

    def test_super_admin_can_manage_chief_admin(self):
        self._make_role_user("profile_super_actor", UserRole.SUPER_ADMIN, superuser=True)
        chief = self._make_role_user("profile_super_target", UserRole.CHIEF_ADMIN)

        self.client.login(username="profile_super_actor", password="StrongPass123")
        response = self.client.post(
            reverse("profiles:profile_admin_update", args=[chief.id]),
            {"admin_section": "identity", "username": "profile_super_target", "role": UserRole.ADMIN},
        )

        self.assertEqual(response.status_code, 302)
        chief.profile_v2.refresh_from_db()
        self.assertEqual(chief.profile_v2.role, UserRole.ADMIN)

    def test_admin_identity_form_hides_role_choices_without_actor(self):
        target = User.objects.create_user(username="profile_role_choice_no_actor", password="StrongPass123")

        form = AdminProfileIdentityForm(
            target_user=target,
            target_profile=target.profile_v2,
            actor_user=None,
        )

        self.assertEqual(list(form.fields["role"].choices), [])

    def test_profile_theme_preferences_show_only_purchased_or_public_themes(self):
        # Theme preferences moved from profile page to settings page
        update_module_states({"store": True})
        user = User.objects.create_user(username="theme_profile_user", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=1)
        UserSubscription.objects.create(user=user, plan=plan)
        listing = ThemeStoreListing.objects.get(key="steam-catalog-price-graph-theme")
        self.client.login(username="theme_profile_user", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile_settings"), HTTP_X_DASHBOARD_PARTIAL="1")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, listing.title)

        UserThemePurchase.objects.create(
            user=user,
            listing=listing,
            theme_key=listing.theme_key,
            amount_rub=listing.price_rub,
        )
        response = self.client.get(reverse("profiles:profile_settings"), HTTP_X_DASHBOARD_PARTIAL="1")
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


class ProfileDirectChatTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="direct_user", password="StrongPass123")
        self.target = User.objects.create_user(username="direct_target", password="StrongPass123")

    def test_start_direct_chat_requires_login(self):
        response = self.client.post(reverse("profiles:profile_start_direct_chat", args=[self.target.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SocialSpace.objects.filter(space_type=SocialSpace.SpaceType.DIRECT).exists())

    def test_start_direct_chat_cannot_target_self(self):
        self.client.login(username="direct_user", password="StrongPass123")

        response = self.client.post(reverse("profiles:profile_start_direct_chat", args=[self.user.id]))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialSpace.objects.filter(space_type=SocialSpace.SpaceType.DIRECT).exists())

    def test_start_direct_chat_missing_target_returns_404(self):
        self.client.login(username="direct_user", password="StrongPass123")

        response = self.client.post(reverse("profiles:profile_start_direct_chat", args=[999999]))

        self.assertEqual(response.status_code, 404)

    def test_start_direct_chat_creates_space_and_redirects_to_detail(self):
        self.client.login(username="direct_user", password="StrongPass123")

        response = self.client.post(reverse("profiles:profile_start_direct_chat", args=[self.target.id]))

        space = SocialSpace.objects.get(space_type=SocialSpace.SpaceType.DIRECT)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_space_url(space))
        self.assertEqual(space.memberships.count(), 2)
        self.assertTrue(SocialMembership.objects.filter(space=space, user=self.user).exists())
        self.assertTrue(SocialMembership.objects.filter(space=space, user=self.target).exists())

    def test_start_direct_chat_reuses_existing_space(self):
        self.client.login(username="direct_user", password="StrongPass123")
        first = self.client.post(reverse("profiles:profile_start_direct_chat", args=[self.target.id]))
        second = self.client.post(reverse("profiles:profile_start_direct_chat", args=[self.target.id]))

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(SocialSpace.objects.filter(space_type=SocialSpace.SpaceType.DIRECT).count(), 1)
        self.assertEqual(first["Location"], second["Location"])

    def test_profile_detail_shows_message_button_for_foreign_user(self):
        admin = User.objects.create_user(username="direct_admin", password="StrongPass123")
        admin.profile_v2.role = UserRole.ADMIN
        admin.profile_v2.save(update_fields=["role"])
        self.client.login(username="direct_admin", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile_detail", args=[self.target.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("profiles:profile_start_direct_chat", args=[self.target.id]))
        self.assertContains(response, "Написать")

    def test_profile_owner_does_not_show_message_button_to_self(self):
        self.client.login(username="direct_user", password="StrongPass123")

        response = self.client.get(reverse("profiles:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("profiles:profile_start_direct_chat", args=[self.user.id]))


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
        self.assertContains(response, "img/placeholders/no_image.jpg")

    def test_profile_page_contains_custom_avatar_upload_ui(self):
        # Avatar upload UI moved from profile page to settings page
        self.client.login(username="avatar_patch_user", password="StrongPass123")
        response = self.client.get(reverse("profiles:profile_settings"), HTTP_X_DASHBOARD_PARTIAL="1")

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

            # Invalid file → redirect to settings with error message
            self.assertEqual(response.status_code, 302)
            self.user.profile_v2.refresh_from_db()
            self.assertFalse(bool(self.user.profile_v2.uploaded_avatar))
            # Check error message stored in session
            from django.contrib.messages import get_messages
            msgs = [str(m) for m in get_messages(response.wsgi_request)]
            self.assertTrue(any("Не удалось обновить аватар." in m for m in msgs))

    def test_avatar_source_priority_uploaded_then_steam_then_default(self):
        profile = self.user.profile_v2
        profile.uploaded_avatar = None
        profile.steam_avatar = ""
        profile.save(update_fields=["uploaded_avatar", "steam_avatar"])
        default_avatar = get_profile_avatar_url(profile)
        self.assertTrue(default_avatar.endswith("img/placeholders/no_image.jpg"))

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


class ProfileVisibilityTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username="vis_viewer", password="StrongPass123")
        self.target = User.objects.create_user(username="vis_target", password="StrongPass123")
        self.target.profile_v2.steam_personaname = "Hidden Target"
        self.target.profile_v2.save(update_fields=["steam_personaname"])
        self.url = reverse("profiles:profile_detail", args=[self.target.id])
        self.client.login(username="vis_viewer", password="StrongPass123")

    def _set_profile_privacy(self, value):
        from modules.v2.social_friends.services import update_privacy_settings

        update_privacy_settings(self.target, who_can_see_profile=value)

    def test_friends_only_profile_renders_closed_page_with_friend_action(self):
        from modules.v2.social_friends.models import DMPrivacy

        self._set_profile_privacy(DMPrivacy.FRIENDS_ONLY)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "profiles/partials/profile_closed_content.html")
        self.assertContains(response, "Профиль виден только друзьям", status_code=403)
        self.assertContains(response, "Hidden Target", status_code=403)
        self.assertContains(
            response,
            reverse("social_friends:send_friend_request", args=[self.target.id]),
            status_code=403,
        )

    def test_friends_only_profile_opens_for_friend(self):
        from modules.v2.social_friends.models import DMPrivacy
        from modules.v2.social_friends.services import accept_friend_request, send_friend_request

        self._set_profile_privacy(DMPrivacy.FRIENDS_ONLY)
        friend_request = send_friend_request(self.viewer, self.target)
        accept_friend_request(self.target, friend_request.pk)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "profiles/partials/profile_content.html")

    def test_hidden_profile_is_closed_even_for_friend(self):
        from modules.v2.social_friends.models import DMPrivacy
        from modules.v2.social_friends.services import accept_friend_request, send_friend_request

        self._set_profile_privacy(DMPrivacy.NOBODY)
        friend_request = send_friend_request(self.viewer, self.target)
        accept_friend_request(self.target, friend_request.pk)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Профиль скрыт", status_code=403)

    def test_blocked_viewer_sees_closed_page_without_identity(self):
        from modules.v2.social_friends.services import block_user

        block_user(self.target, self.viewer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Профиль недоступен", status_code=403)
        self.assertNotContains(response, "Hidden Target", status_code=403)

    def test_moderator_bypasses_profile_privacy(self):
        from modules.v2.social_friends.models import DMPrivacy

        self._set_profile_privacy(DMPrivacy.NOBODY)
        self.viewer.profile_v2.role = UserRole.MOD
        self.viewer.profile_v2.save(update_fields=["role"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_owner_always_sees_own_profile(self):
        from modules.v2.social_friends.models import DMPrivacy

        self._set_profile_privacy(DMPrivacy.NOBODY)
        self.client.login(username="vis_target", password="StrongPass123")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_closed_page_renders_as_dashboard_partial(self):
        from modules.v2.social_friends.models import DMPrivacy

        self._set_profile_privacy(DMPrivacy.NOBODY)

        response = self.client.get(self.url, HTTP_X_DASHBOARD_PARTIAL="1")

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "profiles/partials/profile_closed_content.html")

