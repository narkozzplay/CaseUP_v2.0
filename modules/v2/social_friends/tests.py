from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import DMPrivacy, Friendship, FriendRequest, FriendRequestPrivacy, UserBlock, UserPrivacySettings
from .services import (
    accept_friend_request,
    are_friends,
    block_user,
    can_send_direct,
    cancel_friend_request,
    decline_friend_request,
    get_friendship_context,
    get_or_create_privacy_settings,
    is_blocked,
    remove_friend,
    send_friend_request,
    unblock_user,
    update_privacy_settings,
)


User = get_user_model()


def _make_user(username: str) -> User:
    return User.objects.create_user(username=username, password="pass")


class FriendRequestServiceTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice")
        self.bob = _make_user("bob")
        self.carol = _make_user("carol")

    def test_send_request_creates_pending(self):
        req = send_friend_request(self.alice, self.bob)
        self.assertEqual(req.status, FriendRequest.Status.PENDING)
        self.assertEqual(req.from_user, self.alice)
        self.assertEqual(req.to_user, self.bob)

    def test_send_request_to_self_raises(self):
        with self.assertRaises(ValueError):
            send_friend_request(self.alice, self.alice)

    def test_send_duplicate_returns_existing(self):
        req1 = send_friend_request(self.alice, self.bob)
        req2 = send_friend_request(self.alice, self.bob)
        self.assertEqual(req1.pk, req2.pk)

    def test_send_after_cancel_reactivates(self):
        req = send_friend_request(self.alice, self.bob)
        cancel_friend_request(self.alice, req.pk)
        req2 = send_friend_request(self.alice, self.bob)
        self.assertEqual(req.pk, req2.pk)
        self.assertEqual(req2.status, FriendRequest.Status.PENDING)

    def test_send_after_decline_reactivates(self):
        req = send_friend_request(self.alice, self.bob)
        decline_friend_request(self.bob, req.pk)
        req2 = send_friend_request(self.alice, self.bob)
        self.assertEqual(req.pk, req2.pk)
        self.assertEqual(req2.status, FriendRequest.Status.PENDING)

    def test_cancel_by_sender(self):
        req = send_friend_request(self.alice, self.bob)
        cancelled = cancel_friend_request(self.alice, req.pk)
        self.assertEqual(cancelled.status, FriendRequest.Status.CANCELLED)

    def test_cancel_by_non_sender_raises(self):
        req = send_friend_request(self.alice, self.bob)
        with self.assertRaises(PermissionError):
            cancel_friend_request(self.bob, req.pk)

    def test_accept_creates_friendship(self):
        req = send_friend_request(self.alice, self.bob)
        friendship = accept_friend_request(self.bob, req.pk)
        self.assertIsInstance(friendship, Friendship)
        self.assertTrue(are_friends(self.alice, self.bob))

    def test_accept_by_sender_raises(self):
        req = send_friend_request(self.alice, self.bob)
        with self.assertRaises(PermissionError):
            accept_friend_request(self.alice, req.pk)

    def test_decline_by_recipient(self):
        req = send_friend_request(self.alice, self.bob)
        declined = decline_friend_request(self.bob, req.pk)
        self.assertEqual(declined.status, FriendRequest.Status.DECLINED)
        self.assertFalse(are_friends(self.alice, self.bob))

    def test_remove_friend(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        self.assertTrue(are_friends(self.alice, self.bob))
        remove_friend(self.alice, self.bob)
        self.assertFalse(are_friends(self.alice, self.bob))

    def test_are_friends_symmetric(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        self.assertTrue(are_friends(self.alice, self.bob))
        self.assertTrue(are_friends(self.bob, self.alice))

    def test_send_request_blocked_raises(self):
        block_user(self.bob, self.alice)
        with self.assertRaises(PermissionError):
            send_friend_request(self.alice, self.bob)

    def test_send_request_already_friends_raises(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        with self.assertRaises(ValueError):
            send_friend_request(self.alice, self.bob)

    def test_privacy_nobody_blocks_request(self):
        update_privacy_settings(
            self.bob,
            who_can_send_friend_request=FriendRequestPrivacy.NOBODY,
        )
        with self.assertRaises(PermissionError):
            send_friend_request(self.alice, self.bob)


class BlockServiceTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_b")
        self.bob = _make_user("bob_b")

    def test_block_user(self):
        block_user(self.alice, self.bob)
        self.assertTrue(is_blocked(self.alice, self.bob))
        self.assertFalse(is_blocked(self.bob, self.alice))

    def test_block_removes_friendship(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        self.assertTrue(are_friends(self.alice, self.bob))
        block_user(self.alice, self.bob)
        self.assertFalse(are_friends(self.alice, self.bob))

    def test_block_cancels_pending_requests(self):
        req = send_friend_request(self.alice, self.bob)
        block_user(self.alice, self.bob)
        req.refresh_from_db()
        self.assertEqual(req.status, FriendRequest.Status.CANCELLED)

    def test_unblock_user(self):
        block_user(self.alice, self.bob)
        unblock_user(self.alice, self.bob)
        self.assertFalse(is_blocked(self.alice, self.bob))

    def test_block_self_raises(self):
        with self.assertRaises(ValueError):
            block_user(self.alice, self.alice)

    def test_duplicate_block_idempotent(self):
        block_user(self.alice, self.bob)
        ub = block_user(self.alice, self.bob)
        self.assertIsNotNone(ub)
        self.assertEqual(UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)


class CanSendDirectTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_dm")
        self.bob = _make_user("bob_dm")

    def test_can_send_by_default(self):
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertTrue(allowed)
        self.assertEqual(reason, "everyone")

    def test_blocked_target_denies(self):
        block_user(self.alice, self.bob)
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertFalse(allowed)
        self.assertEqual(reason, "blocked_target")

    def test_blocked_by_target_denies(self):
        block_user(self.bob, self.alice)
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertFalse(allowed)
        self.assertEqual(reason, "blocked_by_target")

    def test_friends_allows(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        update_privacy_settings(self.bob, who_can_send_direct=DMPrivacy.NOBODY)
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertTrue(allowed)
        self.assertEqual(reason, "friends")

    def test_privacy_friends_only_denies_non_friend(self):
        update_privacy_settings(self.bob, who_can_send_direct=DMPrivacy.FRIENDS_ONLY)
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertFalse(allowed)
        self.assertEqual(reason, "privacy_friends_only")

    def test_privacy_nobody_denies(self):
        update_privacy_settings(self.bob, who_can_send_direct=DMPrivacy.NOBODY)
        allowed, reason = can_send_direct(self.alice, self.bob)
        self.assertFalse(allowed)
        self.assertEqual(reason, "privacy_nobody")

    def test_self_denies(self):
        allowed, reason = can_send_direct(self.alice, self.alice)
        self.assertFalse(allowed)
        self.assertEqual(reason, "self")


class PrivacySettingsTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_ps")

    def test_get_or_create_returns_defaults(self):
        settings = get_or_create_privacy_settings(self.alice)
        self.assertEqual(settings.who_can_send_friend_request, FriendRequestPrivacy.EVERYONE)
        self.assertEqual(settings.who_can_send_direct, DMPrivacy.EVERYONE)
        self.assertEqual(settings.who_can_see_online, DMPrivacy.EVERYONE)
        self.assertEqual(settings.who_can_see_profile, DMPrivacy.EVERYONE)

    def test_update_privacy_settings(self):
        update_privacy_settings(
            self.alice,
            who_can_send_direct=DMPrivacy.FRIENDS_ONLY,
            who_can_see_online=DMPrivacy.NOBODY,
        )
        settings = get_or_create_privacy_settings(self.alice)
        self.assertEqual(settings.who_can_send_direct, DMPrivacy.FRIENDS_ONLY)
        self.assertEqual(settings.who_can_see_online, DMPrivacy.NOBODY)
        self.assertEqual(settings.who_can_send_friend_request, FriendRequestPrivacy.EVERYONE)

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            update_privacy_settings(self.alice, who_can_send_direct="invalid_value")


class FriendshipContextTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_ctx")
        self.bob = _make_user("bob_ctx")

    def test_context_self_returns_empty(self):
        ctx = get_friendship_context(self.alice, self.alice)
        self.assertTrue(ctx["is_self"])
        self.assertFalse(ctx["can_send_friend_request"])

    def test_context_stranger_can_request(self):
        ctx = get_friendship_context(self.alice, self.bob)
        self.assertTrue(ctx["can_send_friend_request"])
        self.assertFalse(ctx["is_friend"])

    def test_context_friend(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        ctx = get_friendship_context(self.alice, self.bob)
        self.assertTrue(ctx["is_friend"])
        self.assertTrue(ctx["can_remove_friend"])
        self.assertFalse(ctx["can_send_friend_request"])

    def test_context_blocked(self):
        block_user(self.alice, self.bob)
        ctx = get_friendship_context(self.alice, self.bob)
        self.assertTrue(ctx["is_blocked"])
        self.assertFalse(ctx["can_send_friend_request"])
        self.assertTrue(ctx["can_unblock"])

    def test_context_pending_sent(self):
        send_friend_request(self.alice, self.bob)
        ctx = get_friendship_context(self.alice, self.bob)
        self.assertIsNotNone(ctx["pending_sent"])
        self.assertFalse(ctx["can_send_friend_request"])


class FriendsDMIntegrationTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_int")
        self.bob = _make_user("bob_int")

    def test_dm_blocked_when_privacy_nobody(self):
        update_privacy_settings(self.bob, who_can_send_direct=DMPrivacy.NOBODY)
        from modules.v2.social.services import get_or_create_direct_space
        with self.assertRaises(PermissionError):
            get_or_create_direct_space(self.alice, self.bob)

    def test_dm_allowed_when_friends_despite_privacy(self):
        update_privacy_settings(self.bob, who_can_send_direct=DMPrivacy.NOBODY)
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        from modules.v2.social.services import get_or_create_direct_space
        space = get_or_create_direct_space(self.alice, self.bob)
        self.assertIsNotNone(space)

    def test_dm_blocked_when_blocker(self):
        block_user(self.alice, self.bob)
        from modules.v2.social.services import get_or_create_direct_space
        with self.assertRaises(PermissionError):
            get_or_create_direct_space(self.alice, self.bob)


PARTIAL_HEADERS = {"HTTP_X_DASHBOARD_PARTIAL": "1"}


class FriendViewTests(TestCase):
    def setUp(self):
        self.alice = _make_user("alice_v")
        self.bob = _make_user("bob_v")
        self.client = Client()
        self.client.force_login(self.alice)

    def test_friends_view_get(self):
        resp = self.client.get(reverse("social_friends:friends"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_friend_requests_view_get(self):
        resp = self.client.get(reverse("social_friends:friend_requests"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_privacy_settings_view_get(self):
        resp = self.client.get(reverse("social_friends:privacy_settings"), **PARTIAL_HEADERS)
        self.assertEqual(resp.status_code, 200)

    def test_send_friend_request_post(self):
        resp = self.client.post(
            reverse("social_friends:send_friend_request", args=[self.bob.pk]),
            {"next": reverse("social_friends:friends")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(FriendRequest.objects.filter(from_user=self.alice, to_user=self.bob).exists())

    def test_accept_friend_request_post(self):
        req = send_friend_request(self.alice, self.bob)
        self.client.force_login(self.bob)
        resp = self.client.post(
            reverse("social_friends:accept_friend_request", args=[req.pk]),
            {"next": reverse("social_friends:friend_requests")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(are_friends(self.alice, self.bob))

    def test_block_user_post(self):
        resp = self.client.post(
            reverse("social_friends:block_user", args=[self.bob.pk]),
            {"next": reverse("social_friends:friends")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(is_blocked(self.alice, self.bob))

    def test_unblock_user_post(self):
        block_user(self.alice, self.bob)
        resp = self.client.post(
            reverse("social_friends:unblock_user", args=[self.bob.pk]),
            {"next": reverse("social_friends:friends")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(is_blocked(self.alice, self.bob))

    def test_remove_friend_post(self):
        req = send_friend_request(self.alice, self.bob)
        accept_friend_request(self.bob, req.pk)
        resp = self.client.post(
            reverse("social_friends:remove_friend", args=[self.bob.pk]),
            {"next": reverse("social_friends:friends")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(are_friends(self.alice, self.bob))

    def test_views_require_login(self):
        anon_client = Client()
        urls = [
            reverse("social_friends:friends"),
            reverse("social_friends:friend_requests"),
            reverse("social_friends:privacy_settings"),
        ]
        for url in urls:
            resp = anon_client.get(url)
            self.assertIn(resp.status_code, [302, 403])
