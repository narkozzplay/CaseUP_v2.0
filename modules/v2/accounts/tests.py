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

class SteamLoginConsentTests(TestCase):
    """The login page collects consent; the real social-auth pipeline enforces and records it."""

    STEAM_ID = "76561199703670668"
    STEAM_URL = "https://steamcommunity.com/openid/login?openid.mode=checkid_setup"

    def setUp(self):
        # Starting an OpenID login asks Steam how to redirect; keep tests offline.
        from unittest import mock

        from social_core.backends.steam import SteamOpenId

        for name, value in (("auth_url", self.STEAM_URL), ("uses_redirect", True)):
            patcher = mock.patch.object(SteamOpenId, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _post_login(self, **data):
        return self.client.post(reverse("accounts:login"), data)

    def test_login_page_shows_both_consents_with_document_links(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, 'name="accept_agreement"')
        self.assertContains(response, 'name="accept_privacy"')
        for name in ("frontend:agreement", "frontend:privacy", "frontend:cookies", "frontend:pd_consent"):
            self.assertContains(response, reverse(name))

    def test_steam_sign_in_does_not_start_without_both_boxes(self):
        from .consent import SESSION_KEY

        response = self._post_login(accept_agreement="on")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "согласие на обработку персональных данных")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_accepted_consent_starts_steam_sign_in_and_keeps_safe_next(self):
        from frontend.legal import AGREEMENT

        from .consent import SESSION_KEY

        response = self._post_login(accept_agreement="on", accept_privacy="on", next="/cases/")

        # The consent POST itself starts social-auth (it only accepts POST since social-auth-app-django 6).
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.STEAM_URL)
        self.assertEqual(self.client.session[SESSION_KEY]["agreement_version"], AGREEMENT.version)
        self.assertEqual(self.client.session.get("next"), "/cases/")

    def test_unsafe_next_is_not_kept(self):
        self._post_login(accept_agreement="on", accept_privacy="on", next="https://evil.example/")

        self.assertNotEqual(self.client.session.get("next"), "https://evil.example/")

    def test_no_page_links_to_steam_sign_in_with_get(self):
        # GET /oauth/login/steam/ answers 405 since social-auth-app-django 6: every entry point must POST.
        begin_url = reverse("social_auth:begin", args=["steam"])
        self.assertEqual(self.client.get(begin_url).status_code, 405)
        for name in ("accounts:login", "accounts:register", "frontend:home"):
            # The register page answers 403 on purpose (Steam-only sign-up), so check the body directly.
            self.assertNotIn(f'href="{begin_url}"', self.client.get(reverse(name)).content.decode(), name)

    # ---- real pipeline -------------------------------------------------------------------

    def _complete_steam_login(self):
        """Run social-core's real pipeline for a Steam login, faking only the network calls to Steam."""
        from unittest import mock

        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from social_core.backends.steam import SteamOpenId
        from social_django.utils import load_backend, load_strategy

        request = RequestFactory().get("/oauth/complete/steam/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.update(dict(self.client.session))
        request.user = AnonymousUser()
        strategy = load_strategy(request)
        backend = load_backend(strategy, "steam", redirect_uri="/oauth/complete/steam/")

        response = mock.Mock(identity_url=f"https://steamcommunity.com/openid/id/{self.STEAM_ID}")
        response.endpoint = mock.Mock(claimed_id=response.identity_url)

        def fake_auth_complete(self_backend, *args, **kwargs):
            return self_backend.strategy.authenticate(self_backend, response=response)

        details = {"username": "steam_user", "email": "", "fullname": "", "first_name": "", "last_name": ""}
        with mock.patch.object(SteamOpenId, "auth_complete", fake_auth_complete), mock.patch.object(
            SteamOpenId, "get_user_details", return_value=details
        ), mock.patch.object(SteamOpenId, "extra_data", return_value={}):
            result = backend.complete()
        return result, request

    def test_direct_steam_link_without_consent_is_sent_back_to_login(self):
        from django.contrib.auth import get_user_model

        result, _request = self._complete_steam_login()

        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], reverse("accounts:login") + "?consent=required")
        self.assertFalse(get_user_model().objects.filter(username=f"steam_{self.STEAM_ID}").exists())

    def test_consent_is_recorded_for_the_new_user_and_cleared_from_session(self):
        from frontend.legal import PRIVACY

        from .consent import SESSION_KEY
        from .models import LegalConsent

        self._post_login(accept_agreement="on", accept_privacy="on")
        user, request = self._complete_steam_login()

        self.assertEqual(user.username, f"steam_{self.STEAM_ID}")
        consent = LegalConsent.objects.get(user=user)
        self.assertEqual(consent.privacy_version, PRIVACY.version)
        self.assertTrue(consent.is_adult_confirmed)
        self.assertNotIn(SESSION_KEY, request.session)

    def test_returning_user_with_current_consent_signs_in_without_ticking_again(self):
        self._post_login(accept_agreement="on", accept_privacy="on")
        first_user, _request = self._complete_steam_login()
        self.client.logout()

        user, _request = self._complete_steam_login()

        self.assertEqual(user.pk, first_user.pk)

    def test_returning_user_is_asked_again_after_documents_change(self):
        from .models import LegalConsent

        self._post_login(accept_agreement="on", accept_privacy="on")
        user, _request = self._complete_steam_login()
        LegalConsent.objects.filter(user=user).update(agreement_version="2020-01-01")
        self.client.logout()

        result, _request = self._complete_steam_login()

        self.assertEqual(result["Location"], reverse("accounts:login") + "?consent=required")
