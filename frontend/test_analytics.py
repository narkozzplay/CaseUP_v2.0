from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from frontend.analytics import CONSENT_COOKIE, analytics_context

METRIKA_TAG = "mc.yandex.ru/metrika/tag.js"


@override_settings(YANDEX_METRIKA_ID="56442064")
class YandexMetrikaTests(TestCase):
    def test_counter_waits_for_consent(self):
        page = self.client.get(reverse("frontend:home"))
        self.assertContains(page, "data-cookie-consent")
        self.assertContains(page, "caseup_analytics_consent")
        self.assertNotContains(page, METRIKA_TAG)

        self.client.cookies[CONSENT_COOKIE] = "1"
        page = self.client.get(reverse("frontend:home"))
        self.assertNotContains(page, "data-cookie-consent")
        self.assertContains(page, METRIKA_TAG)
        self.assertContains(page, "ym(56442064, 'init'")
        self.assertContains(page, "webvisor: true")
        self.assertContains(page, "clickmap: true")

    def test_admin_areas_are_excluded(self):
        factory = RequestFactory()
        for path in ("/admin/", "/control-center/", "/en/control-center/", "/en/admin/"):
            request = factory.get(path)
            request.COOKIES[CONSENT_COOKIE] = "1"
            self.assertEqual(analytics_context(request), {"metrika_id": "", "analytics_ask_consent": False})

    @override_settings(YANDEX_METRIKA_ID="")
    def test_no_counter_no_bar(self):
        page = self.client.get(reverse("frontend:home"))
        self.assertNotContains(page, "data-cookie-consent")
        self.assertNotContains(page, METRIKA_TAG)

    def test_policies_mention_metrika(self):
        self.assertContains(self.client.get(reverse("frontend:cookies")), "Яндекс Метрика")
        self.assertContains(self.client.get(reverse("frontend:privacy")), "Яндекс Метрика")
