from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from .context_processors import social_sidebar
from .models import SocialMembership, SocialSpace
from .services import create_social_space, list_user_spaces


User = get_user_model()


class SocialLayerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="social_user", password="StrongPass123")
        self.member = User.objects.create_user(username="social_member", password="StrongPass123")
        self.other = User.objects.create_user(username="social_other", password="StrongPass123")

    def test_social_routes_exist(self):
        self.assertEqual(reverse("social:chats"), "/social/chats/")
        self.assertEqual(reverse("social:groups"), "/social/groups/")
        self.assertEqual(reverse("social:conversations"), "/social/conversations/")

    def test_social_pages_require_login(self):
        for route_name in ("social:chats", "social:groups", "social:conversations"):
            response = self.client.get(reverse(route_name))
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
            self.assertContains(response, "Stage 2")

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
        create_social_space(
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
