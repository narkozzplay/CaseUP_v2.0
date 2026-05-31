from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


User = get_user_model()


class SocialPlaceholderTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="social_user", password="StrongPass123")

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

    def test_groups_placeholder_available_for_authenticated_user(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:groups"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Группы")
        self.assertContains(response, "Групп пока нет")
        self.assertContains(response, reverse("social:chats"))

    def test_conversations_placeholder_available_for_authenticated_user(self):
        self.client.login(username="social_user", password="StrongPass123")

        response = self.client.get(reverse("social:conversations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Беседы")
        self.assertContains(response, "Бесед пока нет")
        self.assertContains(response, reverse("social:chats"))
