from datetime import timedelta
from io import BytesIO
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from PIL import Image

from .context_processors import social_sidebar
from .models import SocialMembership, SocialMessage, SocialSpace
from .services import (
    add_space_member,
    block_space_member,
    build_members_manage_url,
    build_space_cards,
    build_space_detail_context,
    build_space_presentation,
    build_space_settings_url,
    build_space_url,
    change_member_role,
    count_unread_messages_for_user,
    create_social_space,
    create_space_message,
    get_or_create_direct_space,
    leave_space,
    list_space_messages,
    list_user_spaces,
    mark_space_read,
    remove_space_member,
    transfer_space_ownership,
    unblock_space_member,
)


User = get_user_model()


def _build_test_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(40, 72, 132)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_1X1_BYTES = _build_test_png_bytes()


class SocialLayerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="social_user", password="StrongPass123")
        self.member = User.objects.create_user(username="social_member", password="StrongPass123")
        self.other = User.objects.create_user(username="social_other", password="StrongPass123")
        self.media_root = tempfile.mkdtemp(prefix="caseup-social-tests-")
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

    def _set_profile_identity(self, user, *, display_name="", avatar_url=""):
        profile, _created = UserProfile.objects.get_or_create(user=user)
        profile.steam_personaname = display_name
        profile.steam_avatar = avatar_url
        profile.save(update_fields=["steam_personaname", "steam_avatar"])
        return profile

    def _avatar_upload(self, name="avatar.png"):
        return SimpleUploadedFile(name, PNG_1X1_BYTES, content_type="image/png")

    def test_social_routes_exist(self):
        self.assertEqual(reverse("social:chats"), "/social/chats/")
        self.assertEqual(reverse("social:groups"), "/social/groups/")
        self.assertEqual(reverse("social:group_create"), "/social/groups/create/")
        self.assertEqual(reverse("social:conversations"), "/social/conversations/")
        self.assertEqual(reverse("social:conversation_create"), "/social/conversations/create/")
        self.assertEqual(
            reverse("social:space_message_create", args=[1, "demo"]),
            "/social/spaces/1/demo/messages/send/",
        )
        self.assertEqual(reverse("social:space_members_manage", args=[1, "demo"]), "/social/spaces/1/demo/members/")
        self.assertEqual(reverse("social:space_member_add", args=[1, "demo"]), "/social/spaces/1/demo/members/add/")
        self.assertEqual(reverse("social:space_member_remove", args=[1, "demo", 2]), "/social/spaces/1/demo/members/2/remove/")
        self.assertEqual(reverse("social:space_member_role", args=[1, "demo", 2]), "/social/spaces/1/demo/members/2/role/")
        self.assertEqual(reverse("social:space_member_block", args=[1, "demo", 2]), "/social/spaces/1/demo/members/2/block/")
        self.assertEqual(reverse("social:space_member_unblock", args=[1, "demo", 2]), "/social/spaces/1/demo/members/2/unblock/")
        self.assertEqual(
            reverse("social:space_member_transfer_owner", args=[1, "demo", 2]),
            "/social/spaces/1/demo/members/2/transfer-owner/",
        )
        self.assertEqual(reverse("social:space_leave", args=[1, "demo"]), "/social/spaces/1/demo/leave/")
        self.assertEqual(reverse("social:space_settings", args=[1, "demo"]), "/social/spaces/1/demo/settings/")

    def test_social_pages_require_login(self):
        for route_name in ("social:chats", "social:groups", "social:group_create", "social:conversations", "social:conversation_create"):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("social:space_message_create", args=[1, "demo"]))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("social:space_members_manage", args=[1, "demo"]))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("social:space_settings", args=[1, "demo"]))
        self.assertEqual(response.status_code, 302)

    def test_chats_placeholder_available_for_authenticated_user(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:chats"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Communication")
        self.assertContains(response, "Чатов пока нет")
        self.assertContains(response, reverse("social:groups"))
        self.assertContains(response, reverse("social:conversations"))

    def test_empty_states_available_for_authenticated_user(self):
        self.client.login(username="social_user", password="StrongPass123")

        expected = {
            "social:chats": "Чатов пока нет",
            "social:groups": "Групп пока нет",
            "social:conversations": "Бесед пока нет",
        }
        for route_name, empty_text in expected.items():
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, empty_text)

    def test_create_social_space_creates_owner_membership(self):
        space = create_social_space(
            self.user,
            "Raid Coordination",
            SocialSpace.SpaceType.GROUP,
            privacy=SocialSpace.Privacy.PROTECTED,
        )

        self.assertEqual(space.owner, self.user)
        self.assertEqual(space.space_type, SocialSpace.SpaceType.GROUP)
        self.assertEqual(space.privacy, SocialSpace.Privacy.PROTECTED)
        self.assertEqual(space.slug, "raid-coordination")
        membership = SocialMembership.objects.get(space=space, user=self.user)
        self.assertEqual(membership.role, SocialMembership.Role.OWNER)
        self.assertEqual(membership.status, SocialMembership.Status.ACTIVE)

    def test_create_social_space_adds_members_once(self):
        space = create_social_space(
            self.user,
            "Party Chat",
            SocialSpace.SpaceType.CONVERSATION,
            members=[self.member, self.user, self.member],
        )

        self.assertEqual(space.memberships.count(), 2)
        member_membership = SocialMembership.objects.get(space=space, user=self.member)
        self.assertEqual(member_membership.role, SocialMembership.Role.MEMBER)
        self.assertEqual(member_membership.status, SocialMembership.Status.ACTIVE)

    def test_social_page_shows_created_spaces(self):
        space = create_social_space(
            self.user,
            "CaseUP Group",
            SocialSpace.SpaceType.GROUP,
            privacy=SocialSpace.Privacy.PUBLIC,
            description="Coordination hub",
            members=[self.member],
        )
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:groups"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CaseUP Group")
        self.assertContains(response, "Coordination hub")
        self.assertContains(response, "Public")
        self.assertContains(response, "Owner")
        self.assertContains(response, "Участники: 2")
        self.assertContains(response, build_space_url(space))
        self.assertNotContains(response, "Групп пока нет")

    def test_sidebar_context_counts_only_current_user_spaces(self):
        create_social_space(self.user, "Direct One", SocialSpace.SpaceType.DIRECT)
        create_social_space(self.user, "Group One", SocialSpace.SpaceType.GROUP)
        create_social_space(self.other, "Other Group", SocialSpace.SpaceType.GROUP)
        request = self.factory.get("/")
        request.user = self.user

        context = social_sidebar(request)

        self.assertEqual(context["social_counts"]["direct"], 1)
        self.assertEqual(context["social_counts"]["group"], 1)
        self.assertEqual(context["social_counts"]["conversation"], 0)
        self.assertEqual([item["title"] for item in context["social_recent_spaces"]], ["Group One", "Direct One"])

    def test_sidebar_context_ignores_blocked_left_and_limits_recent(self):
        now = timezone.now()
        spaces = []
        for index in range(7):
            space = create_social_space(self.user, f"Group {index}", SocialSpace.SpaceType.GROUP)
            space.last_activity_at = now + timedelta(minutes=index)
            space.save(update_fields=["last_activity_at", "updated_at"])
            spaces.append(space)
        blocked = spaces[0]
        left = spaces[1]
        SocialMembership.objects.filter(space=blocked, user=self.user).update(status=SocialMembership.Status.BLOCKED)
        SocialMembership.objects.filter(space=left, user=self.user).update(status=SocialMembership.Status.LEFT)

        request = self.factory.get("/")
        request.user = self.user
        context = social_sidebar(request)

        self.assertEqual(context["social_counts"]["group"], 5)
        self.assertEqual(len(context["social_recent_spaces"]), 5)
        self.assertNotIn("Group 0", [item["title"] for item in context["social_recent_spaces"]])
        self.assertNotIn("Group 1", [item["title"] for item in context["social_recent_spaces"]])

    def test_slug_is_unique_within_space_type(self):
        first = create_social_space(self.user, "Same Title", SocialSpace.SpaceType.GROUP)
        second = create_social_space(self.user, "Same Title", SocialSpace.SpaceType.GROUP)
        direct = create_social_space(self.user, "Same Title", SocialSpace.SpaceType.DIRECT)

        self.assertEqual(first.slug, "same-title")
        self.assertEqual(second.slug, "same-title-2")
        self.assertEqual(direct.slug, "same-title")

    def test_user_does_not_see_private_spaces_without_membership(self):
        other_space = create_social_space(
            self.other,
            "Hidden Private Group",
            SocialSpace.SpaceType.GROUP,
            privacy=SocialSpace.Privacy.PRIVATE,
        )

        self.assertFalse(list_user_spaces(self.user, space_type=SocialSpace.SpaceType.GROUP).filter(pk=other_space.pk).exists())

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(reverse("social:groups"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Hidden Private Group")

    def test_space_detail_requires_membership_and_redirects_stale_slug(self):
        space = create_social_space(self.user, "Detail Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        self.client.login(username="social_member", password="StrongPass123")

        response = self.client.get(reverse("social:space_detail", args=[space.id, "old-slug"]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_space_url(space))

        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Group")
        self.assertContains(response, "Сообщений пока нет")
        self.assertContains(response, reverse("social:space_message_create", args=[space.id, space.slug]))

        self.client.login(username="social_other", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 404)

    def test_muted_member_can_open_space_but_blocked_member_cannot(self):
        space = create_social_space(self.user, "Muted Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        membership = SocialMembership.objects.get(space=space, user=self.member)
        membership.status = SocialMembership.Status.MUTED
        membership.save(update_fields=["status"])
        self.client.login(username="social_member", password="StrongPass123")

        response = self.client.get(build_space_url(space))

        self.assertEqual(response.status_code, 200)

        membership.status = SocialMembership.Status.BLOCKED
        membership.save(update_fields=["status"])
        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 404)

    def test_left_member_cannot_open_space_and_muted_member_can_send_message(self):
        space = create_social_space(self.user, "Left And Muted Group", SocialSpace.SpaceType.GROUP, members=[self.member, self.other])
        SocialMembership.objects.filter(space=space, user=self.member).update(status=SocialMembership.Status.LEFT)
        SocialMembership.objects.filter(space=space, user=self.other).update(status=SocialMembership.Status.MUTED)

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "left nope"})
        self.assertEqual(response.status_code, 404)

        self.client.login(username="social_other", password="StrongPass123")
        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "muted can write"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SocialMessage.objects.filter(space=space, author=self.other, body="muted can write").exists())

    def test_group_create_creates_owner_membership_and_selected_members(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(
            reverse("social:group_create"),
            {
                "title": "Created Group",
                "description": "Created through form",
                "privacy": SocialSpace.Privacy.PUBLIC,
                "members": [str(self.member.id)],
            },
        )

        space = SocialSpace.objects.get(title="Created Group")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_space_url(space))
        self.assertEqual(SocialMembership.objects.get(space=space, user=self.user).role, SocialMembership.Role.OWNER)
        self.assertTrue(SocialMembership.objects.filter(space=space, user=self.member, role=SocialMembership.Role.MEMBER).exists())

    def test_conversation_create_page_requires_valid_form(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(
            reverse("social:conversation_create"),
            {
                "title": "",
                "description": "Invalid",
                "privacy": SocialSpace.Privacy.PRIVATE,
                "members": [str(self.member.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SocialSpace.objects.filter(description="Invalid").exists())

    def test_conversation_create_rejects_public_privacy(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(
            reverse("social:conversation_create"),
            {
                "title": "Public Conversation",
                "description": "Should stay private-only",
                "privacy": SocialSpace.Privacy.PUBLIC,
                "members": [str(self.member.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SocialSpace.objects.filter(title="Public Conversation").exists())

    def test_create_form_rejects_owner_as_member(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(
            reverse("social:group_create"),
            {
                "title": "Owner Duplicate",
                "description": "",
                "privacy": SocialSpace.Privacy.PRIVATE,
                "members": [str(self.user.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SocialSpace.objects.filter(title="Owner Duplicate").exists())

    def test_get_or_create_direct_space_rejects_self(self):
        with self.assertRaises(ValueError):
            get_or_create_direct_space(self.user, self.user)

    def test_get_or_create_direct_space_is_idempotent_and_order_independent(self):
        first = get_or_create_direct_space(self.user, self.member)
        second = get_or_create_direct_space(self.user, self.member)
        reversed_call = get_or_create_direct_space(self.member, self.user)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, reversed_call.id)
        self.assertEqual(first.space_type, SocialSpace.SpaceType.DIRECT)
        self.assertEqual(first.memberships.count(), 2)
        self.assertEqual(SocialSpace.objects.filter(space_type=SocialSpace.SpaceType.DIRECT).count(), 1)

    def test_stage41_direct_presentation_is_viewer_aware(self):
        self._set_profile_identity(self.user, display_name="Alpha User", avatar_url="https://cdn.example.test/alpha.png")
        self._set_profile_identity(self.member, display_name="Bravo Member", avatar_url="https://cdn.example.test/bravo.png")
        space = get_or_create_direct_space(self.user, self.member)

        user_presentation = build_space_presentation(self.user, space)
        member_presentation = build_space_presentation(self.member, space)

        self.assertEqual(user_presentation["title"], "Bravo Member")
        self.assertEqual(user_presentation["avatar_url"], "https://cdn.example.test/bravo.png")
        self.assertEqual(member_presentation["title"], "Alpha User")
        self.assertEqual(member_presentation["avatar_url"], "https://cdn.example.test/alpha.png")

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bravo Member")
        self.assertContains(response, "https://cdn.example.test/bravo.png")
        self.assertNotContains(response, "Direct:")

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha User")
        self.assertContains(response, "https://cdn.example.test/alpha.png")
        self.assertNotContains(response, "Direct:")

    def test_stage41_direct_list_and_sidebar_use_counterpart_presentation(self):
        self._set_profile_identity(self.member, display_name="Sidebar Buddy", avatar_url="https://cdn.example.test/sidebar-buddy.png")
        space = get_or_create_direct_space(self.user, self.member)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:chats"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sidebar Buddy")
        self.assertContains(response, "https://cdn.example.test/sidebar-buddy.png")
        self.assertNotContains(response, "Direct:")

        request = self.factory.get("/")
        request.user = self.user
        context = social_sidebar(request)
        recent = next(item for item in context["social_recent_spaces"] if item["id"] == space.id)
        self.assertEqual(recent["display_title"], "Sidebar Buddy")
        self.assertEqual(recent["display_avatar_url"], "https://cdn.example.test/sidebar-buddy.png")

    def test_stage41_group_presentation_keeps_group_title_and_does_not_use_owner_avatar_fallback(self):
        owner_profile = self._set_profile_identity(self.user, display_name="Owner Persona", avatar_url="https://cdn.example.test/owner.png")
        space = create_social_space(self.user, "Visible Group", SocialSpace.SpaceType.GROUP, members=[self.member])

        detail_context = build_space_detail_context(self.user, space)
        cards = build_space_cards(self.user, space_type=SocialSpace.SpaceType.GROUP)
        request = self.factory.get("/")
        request.user = self.user
        sidebar_context = social_sidebar(request)

        self.assertEqual(detail_context["space_presentation"]["title"], "Visible Group")
        self.assertFalse(detail_context["space_presentation"]["has_custom_avatar"])
        self.assertNotEqual(detail_context["space_presentation"]["avatar_url"], owner_profile.steam_avatar)
        self.assertEqual(cards[0]["display_title"], "Visible Group")
        self.assertFalse(cards[0]["has_custom_avatar"])
        self.assertNotEqual(cards[0]["display_avatar_url"], owner_profile.steam_avatar)
        self.assertNotEqual(sidebar_context["social_recent_spaces"][0]["display_avatar_url"], owner_profile.steam_avatar)

    def test_stage41_group_custom_avatar_is_used_in_detail_list_and_sidebar(self):
        space = create_social_space(self.user, "Avatar Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        self.client.login(username="social_user", password="StrongPass123")

        with self.settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                build_space_settings_url(space),
                {
                    "title": space.title,
                    "description": "Avatar enabled",
                    "privacy": SocialSpace.Privacy.PRIVATE,
                    "avatar": self._avatar_upload("group-avatar.png"),
                },
            )

            self.assertEqual(response.status_code, 302)
            space.refresh_from_db()
            self.assertTrue(bool(space.avatar))
            self.assertIn("social/avatars/", space.avatar.name)
            detail_context = build_space_detail_context(self.user, space)
            cards = build_space_cards(self.user, space_type=SocialSpace.SpaceType.GROUP)
            request = self.factory.get("/")
            request.user = self.user
            sidebar_context = social_sidebar(request)

        self.assertTrue(detail_context["space_presentation"]["has_custom_avatar"])
        self.assertIn("/media/social/avatars/", detail_context["space_presentation"]["avatar_url"])
        self.assertTrue(cards[0]["has_custom_avatar"])
        self.assertIn("/media/social/avatars/", cards[0]["display_avatar_url"])
        self.assertTrue(sidebar_context["social_recent_spaces"][0]["has_custom_avatar"])
        self.assertIn("/media/social/avatars/", sidebar_context["social_recent_spaces"][0]["display_avatar_url"])

    def test_stage4_owner_and_admin_can_add_members_with_rbac_guards(self):
        admin_user = User.objects.create_user(username="social_admin", password="StrongPass123")
        new_user = User.objects.create_user(username="social_new", password="StrongPass123")
        left_user = User.objects.create_user(username="social_left", password="StrongPass123")
        blocked_user = User.objects.create_user(username="social_blocked", password="StrongPass123")
        space = create_social_space(self.user, "RBAC Add", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        added = add_space_member(admin_user, space, new_user)
        self.assertEqual(added.status, SocialMembership.Status.ACTIVE)
        self.assertEqual(added.role, SocialMembership.Role.MEMBER)

        duplicate = add_space_member(self.user, space, new_user)
        self.assertEqual(duplicate.id, added.id)
        self.assertEqual(SocialMembership.objects.filter(space=space, user=new_user).count(), 1)

        left_membership = add_space_member(self.user, space, left_user)
        left_membership.status = SocialMembership.Status.LEFT
        left_membership.save(update_fields=["status"])
        reactivated = add_space_member(admin_user, space, left_user)
        self.assertEqual(reactivated.status, SocialMembership.Status.ACTIVE)

        blocked_membership = add_space_member(self.user, space, blocked_user)
        blocked_membership.status = SocialMembership.Status.BLOCKED
        blocked_membership.save(update_fields=["status"])
        with self.assertRaises(PermissionError):
            add_space_member(admin_user, space, blocked_user)
        blocked_membership.refresh_from_db()
        self.assertEqual(blocked_membership.status, SocialMembership.Status.BLOCKED)

        with self.assertRaises(PermissionError):
            add_space_member(self.member, space, User.objects.create_user(username="member_add_target"))

        direct = get_or_create_direct_space(self.user, self.other)
        with self.assertRaises(PermissionError):
            add_space_member(self.user, direct, new_user)

    def test_stage4_remove_sets_left_and_respects_admin_limits(self):
        admin_user = User.objects.create_user(username="remove_admin", password="StrongPass123")
        second_admin = User.objects.create_user(username="remove_admin_two", password="StrongPass123")
        target = User.objects.create_user(username="remove_target", password="StrongPass123")
        space = create_social_space(self.user, "RBAC Remove", SocialSpace.SpaceType.GROUP, members=[admin_user, second_admin, target])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)
        change_member_role(self.user, space, second_admin, SocialMembership.Role.ADMIN)

        removed = remove_space_member(admin_user, space, target)
        self.assertEqual(removed.status, SocialMembership.Status.LEFT)
        self.assertTrue(SocialMembership.objects.filter(space=space, user=target).exists())

        with self.assertRaises(PermissionError):
            remove_space_member(admin_user, space, second_admin)
        with self.assertRaises(PermissionError):
            remove_space_member(admin_user, space, self.user)
        with self.assertRaises(PermissionError):
            remove_space_member(self.member, space, admin_user)

        owner_removed = remove_space_member(self.user, space, second_admin)
        self.assertEqual(owner_removed.status, SocialMembership.Status.LEFT)

    def test_stage4_role_changes_block_unblock_transfer_and_leave(self):
        admin_user = User.objects.create_user(username="rbac_admin", password="StrongPass123")
        member_user = User.objects.create_user(username="rbac_member", password="StrongPass123")
        next_owner = User.objects.create_user(username="rbac_next_owner", password="StrongPass123")
        space = create_social_space(self.user, "RBAC Role", SocialSpace.SpaceType.CONVERSATION, members=[admin_user, member_user, next_owner])

        promoted = change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)
        self.assertEqual(promoted.role, SocialMembership.Role.ADMIN)
        demoted = change_member_role(self.user, space, admin_user, SocialMembership.Role.MEMBER)
        self.assertEqual(demoted.role, SocialMembership.Role.MEMBER)
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        with self.assertRaises(PermissionError):
            change_member_role(admin_user, space, member_user, SocialMembership.Role.ADMIN)
        with self.assertRaises(PermissionError):
            change_member_role(member_user, space, admin_user, SocialMembership.Role.MEMBER)

        blocked_member = block_space_member(admin_user, space, member_user)
        self.assertEqual(blocked_member.status, SocialMembership.Status.BLOCKED)
        unblocked_member = unblock_space_member(admin_user, space, member_user)
        self.assertEqual(unblocked_member.status, SocialMembership.Status.ACTIVE)

        blocked_admin = block_space_member(self.user, space, admin_user)
        self.assertEqual(blocked_admin.status, SocialMembership.Status.BLOCKED)
        unblocked_admin = unblock_space_member(self.user, space, admin_user)
        self.assertEqual(unblocked_admin.status, SocialMembership.Status.ACTIVE)

        with self.assertRaises(PermissionError):
            block_space_member(admin_user, space, self.user)
        with self.assertRaises(PermissionError):
            block_space_member(admin_user, space, self.user)

        with self.assertRaises(PermissionError):
            transfer_space_ownership(admin_user, space, next_owner)
        transferred = transfer_space_ownership(self.user, space, next_owner)
        self.assertEqual(transferred.role, SocialMembership.Role.OWNER)
        self.assertEqual(SocialMembership.objects.get(space=space, user=self.user).role, SocialMembership.Role.ADMIN)
        space.refresh_from_db()
        self.assertEqual(space.owner, next_owner)

        with self.assertRaises(PermissionError):
            leave_space(next_owner, space)
        left = leave_space(self.user, space)
        self.assertEqual(left.status, SocialMembership.Status.LEFT)

        direct = get_or_create_direct_space(self.user, self.other)
        with self.assertRaises(PermissionError):
            block_space_member(self.user, direct, self.other)

    def test_create_space_message_creates_message(self):
        space = create_social_space(self.user, "Message Group", SocialSpace.SpaceType.GROUP, members=[self.member])

        message = create_space_message(self.member, space, "  hello team  ")

        self.assertEqual(message.space, space)
        self.assertEqual(message.author, self.member)
        self.assertEqual(message.body, "hello team")
        self.assertEqual(message.message_type, SocialMessage.MessageType.TEXT)
        self.assertFalse(message.is_deleted)

    def test_create_space_message_rejects_empty_body(self):
        space = create_social_space(self.user, "Empty Group", SocialSpace.SpaceType.GROUP)

        with self.assertRaises(ValueError):
            create_space_message(self.user, space, "   ")

        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_create_space_message_rejects_body_over_limit(self):
        space = create_social_space(self.user, "Long Group", SocialSpace.SpaceType.GROUP)

        with self.assertRaises(ValueError):
            create_space_message(self.user, space, "x" * 4001)

        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_create_space_message_requires_membership(self):
        space = create_social_space(self.user, "Members Only", SocialSpace.SpaceType.GROUP)

        with self.assertRaises(PermissionError):
            create_space_message(self.other, space, "hello")

        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_blocked_and_left_members_cannot_send_messages(self):
        space = create_social_space(self.user, "Restricted Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        membership = SocialMembership.objects.get(space=space, user=self.member)

        membership.status = SocialMembership.Status.BLOCKED
        membership.save(update_fields=["status"])
        with self.assertRaises(PermissionError):
            create_space_message(self.member, space, "blocked")

        membership.status = SocialMembership.Status.LEFT
        membership.save(update_fields=["status"])
        with self.assertRaises(PermissionError):
            create_space_message(self.member, space, "left")

        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_message_send_updates_space_activity_and_author_last_read(self):
        space = create_social_space(self.user, "Activity Group", SocialSpace.SpaceType.GROUP)
        old_activity = timezone.now() - timedelta(days=1)
        space.last_activity_at = old_activity
        space.save(update_fields=["last_activity_at", "updated_at"])

        message = create_space_message(self.user, space, "activity bump")

        space.refresh_from_db()
        membership = SocialMembership.objects.get(space=space, user=self.user)
        self.assertGreater(space.last_activity_at, old_activity)
        self.assertIsNotNone(membership.last_read_at)
        self.assertGreaterEqual(membership.last_read_at, message.created_at)

    def test_list_space_messages_returns_chronological_visible_messages(self):
        space = create_social_space(self.user, "Timeline Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        first = create_space_message(self.user, space, "first")
        second = create_space_message(self.member, space, "second")
        deleted = create_space_message(self.user, space, "deleted")
        deleted.is_deleted = True
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        messages = list_space_messages(self.user, space)

        self.assertEqual([message["id"] for message in messages], [first.id, second.id])
        self.assertEqual(messages[0]["body"], "first")
        self.assertEqual(messages[0]["author_id"], self.user.id)
        self.assertTrue(messages[0]["is_own"])
        self.assertEqual(messages[1]["body"], "second")
        self.assertFalse(messages[1]["is_own"])

    def test_unread_counts_ignore_own_messages_and_reset_after_read(self):
        space = create_social_space(self.user, "Unread Group", SocialSpace.SpaceType.GROUP, members=[self.member])
        create_space_message(self.user, space, "own")
        create_space_message(self.member, space, "foreign")

        unread = count_unread_messages_for_user(self.user)

        self.assertEqual(unread["total"], 1)
        self.assertEqual(unread["by_type"]["group"], 1)
        self.assertEqual(unread["by_space_id"][space.id], 1)

        mark_space_read(self.user, space)
        unread = count_unread_messages_for_user(self.user)

        self.assertEqual(unread["total"], 0)
        self.assertEqual(unread["by_space_id"], {})

    def test_unread_counts_group_by_space_type(self):
        direct = create_social_space(self.user, "Direct Unread", SocialSpace.SpaceType.DIRECT, members=[self.member])
        group = create_social_space(self.user, "Group Unread", SocialSpace.SpaceType.GROUP, members=[self.member])
        conversation = create_social_space(self.user, "Conversation Unread", SocialSpace.SpaceType.CONVERSATION, members=[self.member])
        create_space_message(self.member, direct, "direct")
        create_space_message(self.member, group, "group")
        create_space_message(self.member, conversation, "conversation")

        unread = count_unread_messages_for_user(self.user)

        self.assertEqual(unread["total"], 3)
        self.assertEqual(unread["by_type"]["direct"], 1)
        self.assertEqual(unread["by_type"]["group"], 1)
        self.assertEqual(unread["by_type"]["conversation"], 1)

    def test_unread_counts_ignore_blocked_and_left_memberships(self):
        blocked = create_social_space(self.user, "Blocked Unread", SocialSpace.SpaceType.GROUP, members=[self.member])
        left = create_social_space(self.user, "Left Unread", SocialSpace.SpaceType.GROUP, members=[self.member])
        create_space_message(self.member, blocked, "blocked unread")
        create_space_message(self.member, left, "left unread")
        SocialMembership.objects.filter(space=blocked, user=self.user).update(status=SocialMembership.Status.BLOCKED)
        SocialMembership.objects.filter(space=left, user=self.user).update(status=SocialMembership.Status.LEFT)

        unread = count_unread_messages_for_user(self.user)

        self.assertEqual(unread["total"], 0)

    def test_sidebar_context_contains_unread_counts(self):
        space = create_social_space(self.user, "Sidebar Unread", SocialSpace.SpaceType.GROUP, members=[self.member])
        create_space_message(self.member, space, "sidebar unread")
        request = self.factory.get("/")
        request.user = self.user

        context = social_sidebar(request)

        self.assertEqual(context["social_unread_total"], 1)
        self.assertEqual(context["social_unread_counts"]["by_type"]["group"], 1)
        self.assertEqual(context["social_recent_spaces"][0]["unread_count"], 1)

    def test_space_detail_shows_message_empty_state(self):
        space = create_social_space(self.user, "Empty Messages", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(build_space_url(space))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сообщений пока нет")
        self.assertContains(response, "social-message-form")

    def test_space_detail_shows_created_messages(self):
        space = create_social_space(self.user, "Visible Messages", SocialSpace.SpaceType.GROUP, members=[self.member])
        create_space_message(self.member, space, "Hello\nCaseUP")
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(build_space_url(space))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello<br>CaseUP", html=False)
        self.assertContains(response, "social-message")
        self.assertNotContains(response, "author_display_name")
        self.assertNotContains(response, "datetime.datetime")
        self.assertNotContains(response, "messages-stack")

    def test_send_message_requires_membership(self):
        space = create_social_space(self.user, "Send Private", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_other", password="StrongPass123")

        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "nope"})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_send_message_blocked_member_gets_404(self):
        space = create_social_space(self.user, "Blocked Send", SocialSpace.SpaceType.GROUP, members=[self.member])
        SocialMembership.objects.filter(space=space, user=self.member).update(status=SocialMembership.Status.BLOCKED)
        self.client.login(username="social_member", password="StrongPass123")

        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "blocked"})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_send_message_get_returns_method_not_allowed(self):
        space = create_social_space(self.user, "Post Only", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:space_message_create", args=[space.id, space.slug]))

        self.assertEqual(response.status_code, 405)

    def test_send_message_post_redirects_to_detail(self):
        space = create_social_space(self.user, "Send Group", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(
            reverse("social:space_message_create", args=[space.id, space.slug]),
            {"body": "  Sent via view  "},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_space_url(space))
        self.assertTrue(SocialMessage.objects.filter(space=space, author=self.user, body="Sent via view").exists())

    def test_send_message_invalid_form_renders_detail_with_errors(self):
        space = create_social_space(self.user, "Invalid Send", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "   "})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message cannot be empty.")
        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_send_message_over_limit_is_invalid(self):
        space = create_social_space(self.user, "Over Limit Send", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.post(reverse("social:space_message_create", args=[space.id, space.slug]), {"body": "x" * 4001})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message must be 4000 characters or fewer.")
        self.assertFalse(SocialMessage.objects.filter(space=space).exists())

    def test_stage4_members_manage_page_requires_membership_and_forbids_direct(self):
        space = create_social_space(self.user, "Manage Access", SocialSpace.SpaceType.GROUP, members=[self.member])
        direct = get_or_create_direct_space(self.user, self.member)

        response = self.client.get(build_members_manage_url(space))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="social_other", password="StrongPass123")
        response = self.client.get(build_members_manage_url(space))
        self.assertEqual(response.status_code, 404)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_members_manage_url(direct))
        self.assertEqual(response.status_code, 403)

    def test_stage41_space_settings_access_rules(self):
        admin_user = User.objects.create_user(username="settings_admin", password="StrongPass123")
        space = create_social_space(self.user, "Settings Access", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)
        direct = get_or_create_direct_space(self.user, self.member)

        response = self.client.get(build_space_settings_url(space))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_space_settings_url(space))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, "data-avatar-upload")

        self.client.login(username="settings_admin", password="StrongPass123")
        response = self.client.get(build_space_settings_url(space))
        self.assertEqual(response.status_code, 200)

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.get(build_space_settings_url(space))
        self.assertEqual(response.status_code, 403)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_space_settings_url(direct))
        self.assertEqual(response.status_code, 403)

    def test_stage41_space_settings_owner_updates_title_description_privacy_avatar_and_slug(self):
        create_social_space(self.other, "Renamed Group", SocialSpace.SpaceType.GROUP)
        space = create_social_space(self.user, "Original Group", SocialSpace.SpaceType.GROUP)
        self.client.login(username="social_user", password="StrongPass123")

        with self.settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                build_space_settings_url(space),
                {
                    "title": "Renamed Group",
                    "description": "Updated by owner",
                    "privacy": SocialSpace.Privacy.PUBLIC,
                    "avatar": self._avatar_upload("owner-space.png"),
                },
            )

        space.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_space_url(space))
        self.assertEqual(space.title, "Renamed Group")
        self.assertEqual(space.slug, "renamed-group-2")
        self.assertEqual(space.description, "Updated by owner")
        self.assertEqual(space.privacy, SocialSpace.Privacy.PUBLIC)
        self.assertTrue(bool(space.avatar))
        self.assertIn("social/avatars/", space.avatar.name)

    def test_stage41_space_settings_admin_updates_description_and_avatar_only(self):
        admin_user = User.objects.create_user(username="settings_admin_update", password="StrongPass123")
        space = create_social_space(
            self.user,
            "Admin Locked Title",
            SocialSpace.SpaceType.CONVERSATION,
            privacy=SocialSpace.Privacy.PRIVATE,
            members=[admin_user],
        )
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)
        self.client.login(username="settings_admin_update", password="StrongPass123")

        with self.settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                build_space_settings_url(space),
                {
                    "title": "Admin Should Not Rename",
                    "description": "Admin can update this",
                    "privacy": SocialSpace.Privacy.PUBLIC,
                    "avatar": self._avatar_upload("admin-space.png"),
                },
            )

        space.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(space.title, "Admin Locked Title")
        self.assertEqual(space.privacy, SocialSpace.Privacy.PRIVATE)
        self.assertEqual(space.description, "Admin can update this")
        self.assertTrue(bool(space.avatar))

    def test_stage4_member_add_post_works_for_owner_and_admin_only(self):
        admin_user = User.objects.create_user(username="view_add_admin", password="StrongPass123")
        target = User.objects.create_user(username="view_add_target", password="StrongPass123")
        admin_target = User.objects.create_user(username="view_add_admin_target", password="StrongPass123")
        member_target = User.objects.create_user(username="view_add_member_target", password="StrongPass123")
        space = create_social_space(self.user, "View Add", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.post(reverse("social:space_member_add", args=[space.id, space.slug]), {"user": str(target.id)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SocialMembership.objects.filter(space=space, user=target, status=SocialMembership.Status.ACTIVE).exists())

        self.client.login(username="view_add_admin", password="StrongPass123")
        response = self.client.post(reverse("social:space_member_add", args=[space.id, space.slug]), {"user": str(admin_target.id)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SocialMembership.objects.filter(space=space, user=admin_target, status=SocialMembership.Status.ACTIVE).exists())

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.post(reverse("social:space_member_add", args=[space.id, space.slug]), {"user": str(member_target.id)})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialMembership.objects.filter(space=space, user=member_target).exists())

    def test_stage4_member_action_views_follow_rbac(self):
        admin_user = User.objects.create_user(username="view_action_admin", password="StrongPass123")
        target = User.objects.create_user(username="view_action_target", password="StrongPass123")
        owner_target = User.objects.create_user(username="view_owner_target", password="StrongPass123")
        space = create_social_space(self.user, "View Actions", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user, target, owner_target])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.post(
            reverse("social:space_member_role", args=[space.id, space.slug, target.id]),
            {"role": SocialMembership.Role.ADMIN},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialMembership.objects.get(space=space, user=target).role, SocialMembership.Role.ADMIN)

        response = self.client.post(reverse("social:space_member_block", args=[space.id, space.slug, target.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialMembership.objects.get(space=space, user=target).status, SocialMembership.Status.BLOCKED)

        response = self.client.post(reverse("social:space_member_unblock", args=[space.id, space.slug, target.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialMembership.objects.get(space=space, user=target).status, SocialMembership.Status.ACTIVE)

        response = self.client.post(reverse("social:space_member_transfer_owner", args=[space.id, space.slug, owner_target.id]))
        self.assertEqual(response.status_code, 302)
        space.refresh_from_db()
        self.assertEqual(space.owner, owner_target)
        self.assertEqual(SocialMembership.objects.get(space=space, user=self.user).role, SocialMembership.Role.ADMIN)

        self.client.login(username="view_action_admin", password="StrongPass123")
        response = self.client.post(
            reverse("social:space_member_role", args=[space.id, space.slug, self.member.id]),
            {"role": SocialMembership.Role.ADMIN},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(reverse("social:space_member_remove", args=[space.id, space.slug, self.member.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialMembership.objects.get(space=space, user=self.member).status, SocialMembership.Status.LEFT)

    def test_stage4_leave_post_and_get_mutating_route_does_not_mutate(self):
        admin_user = User.objects.create_user(username="leave_admin", password="StrongPass123")
        space = create_social_space(self.user, "Leave View", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.post(reverse("social:space_leave", args=[space.id, space.slug]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SocialMembership.objects.get(space=space, user=self.user).status, SocialMembership.Status.ACTIVE)

        self.client.login(username="leave_admin", password="StrongPass123")
        response = self.client.get(reverse("social:space_leave", args=[space.id, space.slug]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(SocialMembership.objects.get(space=space, user=admin_user).status, SocialMembership.Status.ACTIVE)

        response = self.client.post(reverse("social:space_leave", args=[space.id, space.slug]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("social:groups"))
        self.assertEqual(SocialMembership.objects.get(space=space, user=admin_user).status, SocialMembership.Status.LEFT)

    def test_stage41_social_list_cards_link_member_chips_and_participant_count_by_rbac(self):
        space = create_social_space(self.user, "List Links", SocialSpace.SpaceType.GROUP, members=[self.member])

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(reverse("social:groups"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("profiles:profile_detail", args=[self.member.id]))
        self.assertContains(response, "presence-indicator")
        self.assertContains(response, build_members_manage_url(space))

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.get(reverse("social:groups"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("profiles:profile_detail", args=[self.user.id]))
        self.assertContains(response, f'{build_space_url(space)}#social-members')
        self.assertNotContains(response, build_members_manage_url(space))

    def test_stage4_templates_show_manage_controls_from_prepared_flags(self):
        admin_user = User.objects.create_user(username="template_admin", password="StrongPass123")
        space = create_social_space(self.user, "Template Controls", SocialSpace.SpaceType.GROUP, members=[self.member, admin_user])
        change_member_role(self.user, space, admin_user, SocialMembership.Role.ADMIN)

        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertContains(response, "Управление участниками")
        self.assertContains(response, "social-space-settings-button__icon")
        self.assertNotContains(response, "Участники / управление")
        self.assertContains(response, reverse("profiles:profile_detail", args=[self.member.id]))
        self.assertContains(response, f"ID: {self.member.id}")
        self.assertContains(response, reverse("profiles:profile_start_direct_chat", args=[self.member.id]))
        self.assertNotContains(response, reverse("profiles:profile_start_direct_chat", args=[self.user.id]))
        response = self.client.get(build_members_manage_url(space))
        self.assertContains(response, "Сделать admin")
        self.assertContains(response, "Передать owner")

        self.client.login(username="template_admin", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertContains(response, "Управление участниками")
        self.assertNotContains(response, "Участники / управление")
        response = self.client.get(build_members_manage_url(space))
        self.assertNotContains(response, "Сделать admin")
        self.assertNotContains(response, "Передать owner")

        self.client.login(username="social_member", password="StrongPass123")
        response = self.client.get(build_space_url(space))
        self.assertNotContains(response, "Участники / управление")
        self.assertNotContains(response, "Управление участниками")
        self.assertNotContains(response, "Сделать admin")

        direct = get_or_create_direct_space(self.user, self.member)
        self.client.login(username="social_user", password="StrongPass123")
        response = self.client.get(build_space_url(direct))
        self.assertContains(response, reverse("profiles:profile_detail", args=[self.member.id]))
        self.assertContains(response, f"ID: {self.member.id}")
        self.assertContains(response, reverse("profiles:profile_start_direct_chat", args=[self.member.id]))
        self.assertNotContains(response, reverse("profiles:profile_start_direct_chat", args=[self.user.id]))
        self.assertNotContains(response, "Участники / управление")
        self.assertNotContains(response, "Управление участниками")
        self.assertNotContains(response, "Сделать admin")
