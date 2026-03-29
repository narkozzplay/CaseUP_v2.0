from django.http import HttpRequest
from django.test import SimpleTestCase

from frontend.templatetags.frontend_i18n import switch_lang_url


class FrontendI18nTests(SimpleTestCase):
    def _request(self, full_path: str) -> HttpRequest:
        request = HttpRequest()
        request.get_full_path = lambda: full_path
        return request

    def test_switch_lang_url_from_ru_to_en(self):
        request = self._request("/profile/")
        self.assertEqual(switch_lang_url(request, "en"), "/en/profile/")

    def test_switch_lang_url_from_en_to_ru(self):
        request = self._request("/en/profile/")
        self.assertEqual(switch_lang_url(request, "ru"), "/profile/")

    def test_switch_lang_url_keeps_querystring(self):
        request = self._request("/en/steam-catalog/?q=ak&type=skin")
        self.assertEqual(
            switch_lang_url(request, "ru"),
            "/steam-catalog/?q=ak&type=skin",
        )

    def test_switch_lang_url_root(self):
        request = self._request("/")
        self.assertEqual(switch_lang_url(request, "en"), "/en/")
        self.assertEqual(switch_lang_url(request, "ru"), "/")