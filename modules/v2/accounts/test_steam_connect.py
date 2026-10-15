from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .consent import SESSION_KEY, current_versions, record_consent
from .social_pipeline import prevent_foreign_steam_link

User = get_user_model()


class SteamConnectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="no_steam", password="StrongPass123")
        self.client.force_login(self.user)

    def test_profile_offers_connect_with_consent_boxes(self):
        page = self.client.get(reverse("profiles:profile"))
        self.assertContains(page, reverse("accounts:connect_steam"))
        self.assertContains(page, 'name="accept_agreement"')
        self.assertNotContains(page, reverse("profiles:sync_steam"))

        record_consent(self.user, {**current_versions(), "accepted_at": ""})
        page = self.client.get(reverse("profiles:profile"))
        self.assertContains(page, reverse("accounts:connect_steam"))
        self.assertNotContains(page, 'name="accept_agreement"')

    def test_connect_requires_consent_then_starts_steam_login(self):
        with mock.patch("modules.v2.accounts.views.social_auth_begin", return_value=HttpResponse("to steam")) as begin:
            refused = self.client.post(reverse("accounts:connect_steam"))
            self.assertRedirects(refused, reverse("profiles:profile"), fetch_redirect_response=False)
            begin.assert_not_called()

            started = self.client.post(reverse("accounts:connect_steam"), {"accept_agreement": "on", "accept_privacy": "on"})
        self.assertEqual(started.content, b"to steam")
        self.assertIn(SESSION_KEY, self.client.session)

    def test_connect_is_post_only(self):
        self.assertEqual(self.client.get(reverse("accounts:connect_steam")).status_code, 405)


class ForeignSteamLinkTests(TestCase):
    def _backend(self, owner_id):
        social = SimpleNamespace(user_id=owner_id) if owner_id else None
        storage = SimpleNamespace(user=SimpleNamespace(get_social_auth=lambda provider, uid: social))
        return SimpleNamespace(name="steam", strategy=SimpleNamespace(storage=storage))

    def test_steam_of_another_account_is_refused(self):
        me = User.objects.create_user(username="linker")
        request = RequestFactory().get("/")
        request.session = {}
        request._messages = mock.Mock()
        strategy = SimpleNamespace(request=request, redirect=lambda url: ("redirect", url))
        with mock.patch("django.contrib.messages.error") as error:
            result = prevent_foreign_steam_link(strategy, self._backend(owner_id=me.pk + 100), uid="7656", user=me)
        self.assertEqual(result, ("redirect", reverse("profiles:profile")))
        error.assert_called_once()

        self.assertIsNone(prevent_foreign_steam_link(strategy, self._backend(owner_id=me.pk), uid="7656", user=me))
        self.assertIsNone(prevent_foreign_steam_link(strategy, self._backend(owner_id=None), uid="7656", user=me))
        self.assertIsNone(prevent_foreign_steam_link(strategy, self._backend(owner_id=999), uid="7656", user=None))
