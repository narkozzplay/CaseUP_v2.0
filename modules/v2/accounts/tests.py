from django.test import Client, TestCase
from django.urls import reverse

from .social_pipeline import steam_set_username


class DummyBackend:
    name = "steam"


class DummyEndpoint:
    claimed_id = "https://steamcommunity.com/openid/id/76561199703670668"


class DummySuccessResponse:
    endpoint = DummyEndpoint()


class AccountsTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_login_page_opens(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Steam")

    def test_register_page_disabled(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Ручная регистрация отключена", status_code=403)

    def test_steam_pipeline_uses_steam_id_from_dict_response(self):
        result = steam_set_username(
            strategy=None,
            details={},
            backend=DummyBackend(),
            uid=None,
            response={
                "openid.claimed_id": "https://steamcommunity.com/openid/id/76561199703670668",
            },
        )
        self.assertEqual(result["username"], "steam_76561199703670668")

    def test_steam_pipeline_uses_steam_id_from_success_response(self):
        result = steam_set_username(
            strategy=None,
            details={},
            backend=DummyBackend(),
            uid=None,
            response=DummySuccessResponse(),
        )
        self.assertEqual(result["username"], "steam_76561199703670668")