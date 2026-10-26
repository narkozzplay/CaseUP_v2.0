from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.utils import timezone

from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan

from .models import AdPlacement, AdSettings, HouseBanner
from .services import CONSENT_COOKIE, drop_banner_cache, get_ad_slot, get_feed_every_n, user_sees_ads

User = get_user_model()


class AdVisibilityTests(TestCase):
    def setUp(self):
        drop_banner_cache()
        self.user = User.objects.create_user(username="ads_user", password="x")
        # The seeded promos are covered by HouseBannerTests; here one banner lives in one place.
        HouseBanner.objects.update(is_enabled=False)
        drop_banner_cache()
        self.banner = HouseBanner.objects.create(
            name="Рынок",
            title="Рынок CaseUP",
            text="Продавай скины без комиссии Steam",
            url="/market/",
            button="Открыть",
        )
        self.banner.placements.add(AdPlacement.objects.get(key="catalog_top"))

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

    def test_place_without_banner_renders_nothing(self):
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
        self.assertIn(f'href="/ads/go/{self.banner.pk}/"', html)
        self.assertIn("Убрать рекламу за 99 ₽", html)

    def test_yandex_provider_renders_rtb_block(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        AdPlacement.objects.filter(key="catalog_top").update(block_id="R-A-1234567-1")
        html = self._render(self.user, consent=True)
        self.assertIn('data-ad-block="R-A-1234567-1"', html)
        self.assertNotIn("Рынок CaseUP", html)

    def test_ad_network_waits_for_cookie_consent_and_house_banner_fills_in(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        AdPlacement.objects.filter(key="catalog_top").update(block_id="R-A-1234567-1")
        html = self._render(self.user)
        self.assertNotIn("data-ad-block", html)
        self.assertIn("Рынок CaseUP", html)

    def test_consent_bar_and_network_script_follow_consent(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        self.client.force_login(self.user)
        page = self.client.get("/subscriptions/")
        self.assertContains(page, "data-cookie-consent")
        self.assertNotContains(page, "yandex.ru/ads/system/context.js")
        self.client.cookies[CONSENT_COOKIE] = "1"
        page = self.client.get("/subscriptions/")
        self.assertNotContains(page, "data-cookie-consent")
        self.assertContains(page, "yandex.ru/ads/system/context.js")

    def test_house_banners_need_no_consent_bar(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/subscriptions/"), "data-cookie-consent")

    def test_yandex_provider_without_block_id_shows_house_banner(self):
        AdSettings.objects.filter(singleton_key=1).update(provider=AdSettings.Provider.YANDEX)
        self.assertIn("Рынок CaseUP", self._render(self.user, consent=True))
        self.banner.delete()
        self.assertNotIn("ad-slot", self._render(self.user, consent=True))

    def test_template_tag_renders_nothing_for_subscriber(self):
        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(code="site_no_ads"))
        self.assertNotIn("ad-slot", self._render(self.user))


class HouseBannerTests(TestCase):
    def setUp(self):
        drop_banner_cache()
        self.user = User.objects.create_user(username="banner_user", password="x")
        self.factory = RequestFactory()

    def _request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def _render(self, user, template='{% ad_slot "catalog_top" %}'):
        return Template(template).render(Context({"request": self._request(user)}))

    def test_promo_banners_are_seeded(self):
        urls = set(HouseBanner.objects.filter(is_enabled=True).values_list("url", flat=True))
        self.assertEqual(urls, {"/donate/", "https://t.me/+Oo3Eb-Zc1n00Yzgy", "/subscriptions/"})
        html = self._render(self.user)
        self.assertIn("ad-house", html)

    def test_banners_rotate_without_repeats_on_one_page(self):
        request = self._request(self.user)
        picked = {get_ad_slot(self.user, "catalog_feed", request=request).banner.pk for _ in range(3)}
        self.assertEqual(len(picked), 3)
        # Once every banner was shown, the place still fills.
        self.assertIsNotNone(get_ad_slot(self.user, "catalog_feed", request=request))

    def test_banner_limited_to_its_placements_and_dates(self):
        HouseBanner.objects.update(is_enabled=False)
        drop_banner_cache()
        banner = HouseBanner.objects.create(name="Только график", title="Под графиком", url="/donate/")
        banner.placements.add(AdPlacement.objects.get(key="item_below_chart"))
        self.assertIsNone(get_ad_slot(self.user, "catalog_top"))
        self.assertEqual(get_ad_slot(self.user, "item_below_chart").banner, banner)
        HouseBanner.objects.filter(pk=banner.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
        drop_banner_cache()
        self.assertIsNone(get_ad_slot(self.user, "item_below_chart"))
        HouseBanner.objects.filter(pk=banner.pk).update(ends_at=None, starts_at=timezone.now() + timedelta(days=1))
        drop_banner_cache()
        self.assertIsNone(get_ad_slot(self.user, "item_below_chart"))

    def test_audience_guests_and_users(self):
        HouseBanner.objects.update(is_enabled=False)
        drop_banner_cache()
        HouseBanner.objects.create(name="Гостям", title="Зарегистрируйся", url="/register/", audience=HouseBanner.Audience.GUESTS)
        self.assertIsNone(get_ad_slot(self.user, "catalog_top"))
        self.assertIsNotNone(get_ad_slot(AnonymousUser(), "catalog_top"))
        HouseBanner.objects.update(audience=HouseBanner.Audience.USERS)
        drop_banner_cache()
        self.assertIsNotNone(get_ad_slot(self.user, "catalog_top"))
        self.assertIsNone(get_ad_slot(AnonymousUser(), "catalog_top"))

    def test_ad_free_subscriber_sees_only_marked_banners_without_ad_label(self):
        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(code="site_no_ads"))
        self.assertNotIn("ad-slot", self._render(self.user))
        HouseBanner.objects.filter(url="https://t.me/+Oo3Eb-Zc1n00Yzgy").update(show_to_ad_free=True)
        drop_banner_cache()
        html = self._render(self.user)
        self.assertIn("CaseUP в Telegram", html)
        self.assertNotIn("Реклама", html)
        self.assertNotIn("Убрать рекламу", html)

    def test_feed_check_does_not_use_up_a_banner(self):
        HouseBanner.objects.exclude(url="/donate/").update(is_enabled=False)
        drop_banner_cache()
        request = self._request(self.user)
        self.assertEqual(get_feed_every_n(self.user, request=request), 12)
        self.assertFalse(getattr(request, "_caseup_house_banners_shown", set()))

    def test_impressions_and_clicks_are_counted(self):
        HouseBanner.objects.exclude(url="/donate/").update(is_enabled=False)
        drop_banner_cache()
        banner = HouseBanner.objects.get(url="/donate/")
        self._render(self.user)
        self.client.force_login(self.user)
        response = self.client.get(f"/ads/go/{banner.pk}/")
        self.assertRedirects(response, "/donate/", fetch_redirect_response=False)
        banner.refresh_from_db()
        self.assertEqual((banner.impressions, banner.clicks), (1, 1))

    def test_internal_banner_keeps_visitor_language(self):
        banner = HouseBanner.objects.get(url="/donate/")
        self.assertEqual(self.client.get(f"/en/ads/go/{banner.pk}/")["Location"], "/en/donate/")
        self.assertEqual(self.client.get(f"/ads/go/{banner.pk}/")["Location"], "/donate/")

    def test_external_banner_opens_in_new_tab(self):
        HouseBanner.objects.exclude(url="https://t.me/+Oo3Eb-Zc1n00Yzgy").update(is_enabled=False)
        drop_banner_cache()
        html = self._render(self.user)
        self.assertIn('target="_blank"', html)
        banner = HouseBanner.objects.get(url="https://t.me/+Oo3Eb-Zc1n00Yzgy")
        response = self.client.get(f"/ads/go/{banner.pk}/")
        self.assertEqual(response["Location"], "https://t.me/+Oo3Eb-Zc1n00Yzgy")

    def test_url_must_be_local_or_https(self):
        banner = HouseBanner(name="x", title="x", url="javascript:alert(1)")
        with self.assertRaises(ValidationError):
            banner.full_clean()
        banner.url = "//evil.example"
        with self.assertRaises(ValidationError):
            banner.full_clean()


class AdsControlCenterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="ads_cc_admin", password="x", email="a@example.com")
        self.client.force_login(self.admin)

    def test_page_lists_banners(self):
        response = self.client.get("/control-center/ads/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CaseUP в Telegram")
        self.assertContains(response, "Рекламные места")

    def test_regular_user_is_forbidden(self):
        user = User.objects.create_user(username="ads_cc_user", password="x")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/control-center/ads/").status_code, 403)

    def test_create_toggle_and_delete_banner(self):
        placement = AdPlacement.objects.get(key="sidebar_bottom")
        response = self.client.post("/control-center/ads/banners/new/", {
            "name": "Акция", "title": "Скидка", "url": "/subscriptions/", "audience": "all",
            "weight": 20, "is_enabled": "on", "placements": [placement.pk],
        })
        banner = HouseBanner.objects.get(name="Акция")
        self.assertRedirects(response, f"/control-center/ads/banners/{banner.pk}/", fetch_redirect_response=False)
        self.assertEqual(list(banner.placements.all()), [placement])
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.ADS_UPDATE).exists())

        self.client.post("/control-center/ads/", {"action": "banner_toggle", "banner_id": banner.pk})
        banner.refresh_from_db()
        self.assertFalse(banner.is_enabled)
        self.client.post("/control-center/ads/", {"action": "banner_delete", "banner_id": banner.pk})
        self.assertFalse(HouseBanner.objects.filter(pk=banner.pk).exists())

    def test_settings_and_placements(self):
        self.client.post("/control-center/ads/", {"action": "settings", "provider": "house", "feed_every_n": 6, "show_remove_link": "on"})
        settings_obj = AdSettings.objects.get(singleton_key=1)
        self.assertFalse(settings_obj.is_enabled)
        self.assertEqual(settings_obj.feed_every_n, 6)
        keep = AdPlacement.objects.get(key="catalog_top")
        self.client.post("/control-center/ads/", {"action": "placements", "placement_enabled": [keep.pk]})
        self.assertEqual(list(AdPlacement.objects.filter(is_enabled=True).values_list("key", flat=True)), ["catalog_top"])


class PromoImageTests(TestCase):
    def test_seeded_promos_get_their_illustrations(self):
        for url, name in (("/donate/", "donate"), ("https://t.me/+Oo3Eb-Zc1n00Yzgy", "telegram"), ("/subscriptions/", "no_ads")):
            banner = HouseBanner.objects.get(url=url)
            self.assertIn(f"promo_{name}", banner.image.name)
            banner.image.delete(save=False)


class BannerVideoTests(TestCase):
    def setUp(self):
        drop_banner_cache()
        self.user = User.objects.create_user(username="video_user", password="x")
        HouseBanner.objects.exclude(url="/donate/").update(is_enabled=False)
        drop_banner_cache()
        self.banner = HouseBanner.objects.get(url="/donate/")

    def tearDown(self):
        self.banner.refresh_from_db()
        if self.banner.video:
            self.banner.video.delete(save=False)
        if self.banner.image:
            self.banner.image.delete(save=False)

    def _render(self):
        request = RequestFactory().get("/")
        request.user = self.user
        return Template('{% ad_slot "catalog_top" %}').render(Context({"request": request}))

    def test_video_loops_muted_with_picture_as_poster(self):
        self.banner.video.save("loop.mp4", ContentFile(b"\\x00\\x00\\x00\\x18ftypmp42"), save=True)
        html = self._render()
        self.assertIn("<video", html)
        for attr in ("autoplay", "muted", "loop", "playsinline"):
            self.assertIn(attr, html)
        self.assertIn(f'poster="{self.banner.image.url}"', html)
        self.assertIn('type="video/mp4"', html)
        self.assertIn("ad-house__poster", html)

    def test_banner_without_video_stays_a_picture(self):
        self.banner.video.delete(save=True)
        drop_banner_cache()
        self.assertNotIn("<video", self._render())

    def test_video_needs_a_picture_and_a_known_format(self):
        banner = HouseBanner(name="v", title="v", url="/donate/")
        banner.video.name = "ads/video/loop.mp4"
        with self.assertRaises(ValidationError) as ctx:
            banner.full_clean()
        self.assertIn("image", ctx.exception.message_dict)
        banner.video.name = "ads/video/loop.gif"
        with self.assertRaises(ValidationError) as ctx:
            banner.full_clean()
        self.assertIn("video", ctx.exception.message_dict)


class PromoVideoTests(TestCase):
    def test_seeded_promos_get_their_loops(self):
        for url, name in (("/donate/", "donate"), ("https://t.me/+Oo3Eb-Zc1n00Yzgy", "telegram"), ("/subscriptions/", "no_ads")):
            banner = HouseBanner.objects.get(url=url)
            self.assertIn(f"promo_{name}_loop", banner.video.name)
            self.assertEqual(banner.video_type, "video/mp4")
            banner.video.delete(save=False)
            banner.image.delete(save=False)
