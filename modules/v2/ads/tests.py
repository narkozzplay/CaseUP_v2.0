from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan

from .models import AdPlacement, AdSettings
from .services import CONSENT_COOKIE, get_ad_slot, user_sees_ads

User = get_user_model()


class AdVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ads_user", password="x")
        AdPlacement.objects.filter(key="catalog_top").update(
            house_title="Рынок CaseUP",
            house_text="Продавай скины без комиссии Steam",
            house_url="/market/",
            house_button="Открыть",
        )

    def test_placements_are_seeded(self):
        self.assertEqual(
            set(AdPlacement.objects.values_list("key", flat=True)),
            {"catalog_top", "catalog_feed", "item_below_chart", "sidebar_bottom"},
        )

    def test_ads_are_off_until_enabled(self):
        AdSettings.objects.filter(singleton_key=1).update(is_enabled=False)
        self.assertFalse(user_sees_ads(self.user))

    def test_free_users_and_guests_see_ads(self):
        self.assertTrue(user_sees_ads(self.user))
        self.assertTrue(user_sees_ads(AnonymousUser()))
        self.assertIsNotNone(get_ad_slot(self.user, "catalog_top"))

    def test_slot_without_block_id_renders_nothing(self):
        self.assertIsNone(get_ad_slot(self.user, "item_below_chart"))

    def test_any_paid_plan_removes_ads(self):
        for code in ("site_no_ads", "farmer_start"):
            user = User.objects.create_user(username=f"paid_{code}", password="x")
            assign_user_subscription_plan(user=user, plan=SubscriptionPlan.objects.get(code=code))
            self.assertFalse(user_sees_ads(user), code)

    def test_staff_sees_ads_only_when_asked(self):
        admin = User.objects.create_user(username="ads_admin", password="x", is_staff=True)
        self.assertFalse(user_sees_ads(admin))
        AdSettings.objects.filter(singleton_key=1).update(show_to_staff=True)
        self.assertTrue(user_sees_ads(admin))

    def _render(self, user, *, consent=False):
        request = RequestFactory().get("/")
        if consent:
            request.COOKIES[CONSENT_COOKIE] = "1"
        request.user = user
        return Template('{% ad_slot "catalog_top" %}').render(Context({"request": request}))

    def test_house_banner_renders_with_remove_link(self):
        html = self._render(self.user)
        self.assertIn("Рынок CaseUP", html)
        self.assertIn('href="/market/"', html)
        self.assertIn("Убрать рекламу за 99 ₽", html)

    def test_yandex_provider_renders_rtb_block(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        AdPlacement.objects.filter(key="catalog_top").update(block_id="R-A-1234567-1")
        html = self._render(self.user, consent=True)
        self.assertIn('data-ad-block="R-A-1234567-1"', html)
        self.assertNotIn("Рынок CaseUP", html)

    def test_ad_network_waits_for_cookie_consent(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        AdPlacement.objects.filter(key="catalog_top").update(block_id="R-A-1234567-1")
        self.assertNotIn("ad-slot", self._render(self.user))

    def test_consent_bar_and_network_script_follow_consent(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        self.client.force_login(self.user)
        page = self.client.get("/subscriptions/")
        self.assertContains(page, "data-ad-consent")
        self.assertNotContains(page, "yandex.ru/ads/system/context.js")
        self.client.cookies[CONSENT_COOKIE] = "1"
        page = self.client.get("/subscriptions/")
        self.assertNotContains(page, "data-ad-consent")
        self.assertContains(page, "yandex.ru/ads/system/context.js")

    def test_house_banners_need_no_consent_bar(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/subscriptions/"), "data-ad-consent")

    def test_yandex_provider_without_block_id_renders_nothing(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        self.assertNotIn("ad-slot", self._render(self.user))

    def test_template_tag_renders_nothing_for_subscriber(self):
        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(code="site_no_ads"))
        self.assertNotIn("ad-slot", self._render(self.user))
