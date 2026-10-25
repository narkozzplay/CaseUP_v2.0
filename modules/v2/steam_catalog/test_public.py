from __future__ import annotations

import json
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from modules.v2.subscriptions.models import SubscriptionFeature

from .models import SteamItem, SteamItemPriceHistory

User = get_user_model()


def make_item(name: str, price: str | None = "1250", **extra) -> SteamItem:
    return SteamItem.objects.create(
        market_hash_name=name, name=name, item_type=extra.pop("item_type", "skin"),
        price_rub=Decimal(price) if price else None, **extra,
    )


class GuestCatalogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.item = make_item(
            "AK-47 | Redline (Field-Tested)", family_name="AK-47 | Redline", exterior="Field-Tested", wear_order=3,
            icon_url="abc123",
        )
        SteamItemPriceHistory.objects.create(item=self.item, price_rub=Decimal("1200"), source="test")
        SteamItemPriceHistory.objects.create(item=self.item, price_rub=Decimal("1250"), source="test")
        self.client = Client()

    def test_guest_sees_the_list_and_the_item_page(self):
        page = self.client.get(reverse("steam_catalog:list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.item.get_absolute_url())
        self.assertContains(page, "<title>Цены скинов CS2 в Steam")
        self.assertNotContains(page, 'name="robots"')

        detail = self.client.get(self.item.get_absolute_url())
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "data-price-chart")
        # Analytics stays behind the plan; the star and the alert lead to sign-in.
        self.assertContains(detail, "Анализ цены по подписке")
        self.assertNotContains(detail, "Сырая история цены")
        self.assertNotContains(detail, reverse("steam_catalog:favorite", args=[self.item.pk]))
        self.assertContains(detail, f'{reverse("accounts:login")}?next=')
        self.assertContains(detail, "Войти через Steam")
        self.assertNotContains(detail, "Склад")

    def test_item_address_has_the_name_and_old_addresses_redirect(self):
        self.assertEqual(self.item.slug, "ak-47-redline-field-tested")
        self.assertEqual(self.item.get_absolute_url(), f"/steam-catalog/{self.item.pk}-ak-47-redline-field-tested/")
        stattrak = make_item("StatTrak™ AK-47 | Redline (Field-Tested)")
        self.assertEqual(stattrak.slug, "stattrak-ak-47-redline-field-tested")

        old = self.client.get(reverse("steam_catalog:detail", args=[self.item.pk]), {"range": "month"})
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old["Location"], f"{self.item.get_absolute_url()}?range=month")
        wrong_name = self.client.get(f"/steam-catalog/{self.item.pk}-awp-asiimov/")
        self.assertEqual((wrong_name.status_code, wrong_name["Location"]), (301, self.item.get_absolute_url()))
        self.assertEqual(self.client.get("/steam-catalog/999999-nothing/").status_code, 404)

    def test_item_page_has_its_own_title_description_and_product_data(self):
        detail = self.client.get(self.item.get_absolute_url(), {"range": "year"})
        html = detail.content.decode()
        self.assertIn("<title>AK-47 | Redline (Field-Tested) — цена в Steam сегодня, график", html)
        self.assertIn("Цена AK-47 | Redline (Field-Tested) на торговой площадке Steam сейчас: 1 250 ₽.", html)
        self.assertIn(f'rel="canonical" href="http://testserver{self.item.get_absolute_url()}"', html)
        self.assertIn('property="og:type" content="product"', html)
        self.assertIn('property="og:image" content="https://community.cloudflare.steamstatic.com/economy/image/abc123/512x512"', html)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(data["@type"], "Product")
        self.assertEqual(data["offers"], {
            "@type": "Offer", "price": "1250.00", "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock", "url": f"http://testserver{self.item.get_absolute_url()}",
        })

    def test_filtered_lists_are_not_indexed(self):
        self.assertContains(self.client.get(reverse("steam_catalog:list"), {"q": "redline"}), '<meta name="robots" content="noindex, follow">')
        by_type = self.client.get(reverse("steam_catalog:list"), {"type": "case"})
        self.assertNotContains(by_type, 'name="robots"')
        self.assertContains(by_type, 'rel="canonical" href="http://testserver/steam-catalog/?type=case"')

    def test_guest_is_sent_to_sign_in_when_the_catalog_is_closed_again(self):
        SubscriptionFeature.objects.filter(code="steam_catalog_view").update(free_tier=SubscriptionFeature.FreeTier.REGISTERED)
        response = self.client.get(self.item.get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("accounts:login")))

    def test_signed_in_user_keeps_the_star_and_alert(self):
        self.client.force_login(User.objects.create_user(username="fan", password="x"))
        detail = self.client.get(self.item.get_absolute_url())
        self.assertContains(detail, reverse("steam_catalog:favorite", args=[self.item.pk]))
        self.assertContains(detail, f'{reverse("steam_catalog:alert_rule_create")}?item={self.item.pk}')
        self.assertNotContains(detail, "Войти через Steam")

    def test_sitemap_lists_items_with_a_price(self):
        make_item("Recoil Case", price=None, item_type="case")
        make_item("Hidden Case", item_type="case", is_active=False)
        index = self.client.get("/sitemap.xml")
        self.assertContains(index, "https://testserver/sitemap-items.xml")
        items = self.client.get("/sitemap-items.xml")
        self.assertContains(items, f"<loc>https://testserver{self.item.get_absolute_url()}</loc>")
        self.assertNotContains(items, "recoil-case")
        self.assertNotContains(items, "hidden-case")
        self.assertContains(self.client.get("/sitemap-pages.xml"), "<loc>https://testserver/steam-catalog/</loc>")


@override_settings(STEAM_CATALOG_GUEST_CACHE_SECONDS=600)
class GuestCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.item = make_item("AWP | Asiimov (Battle-Scarred)", family_name="AWP | Asiimov", exterior="Battle-Scarred", wear_order=5)
        SteamItemPriceHistory.objects.create(item=self.item, price_rub=Decimal("7490"), source="test")

    def test_guest_pages_reuse_cached_data(self):
        client = Client()
        client.get(reverse("steam_catalog:list"))
        client.get(self.item.get_absolute_url())
        # Data changes after the first visit; guests keep seeing the cached copy for a while.
        make_item("Recoil Case", item_type="case")
        SteamItemPriceHistory.objects.create(item=self.item, price_rub=Decimal("9999"), source="test")
        self.assertNotContains(client.get(reverse("steam_catalog:list")), "Recoil Case")
        self.assertNotContains(client.get(self.item.get_absolute_url()), "9999.00")

        signed_in = Client()
        signed_in.force_login(User.objects.create_user(username="fan", password="x"))
        self.assertContains(signed_in.get(reverse("steam_catalog:list")), "Recoil Case")

    def test_cached_list_keeps_paging(self):
        for index in range(30):
            make_item(f"Sticker | Test {index:02d}", item_type="sticker")
        client = Client()
        first = client.get(reverse("steam_catalog:list"), {"page": "2"})
        second = client.get(reverse("steam_catalog:list"), {"page": "2"})
        self.assertContains(first, "2 / 2")
        self.assertContains(second, "2 / 2")
        self.assertEqual(first.content.decode().count("catalog-card__link"), second.content.decode().count("catalog-card__link"))


@override_settings(GUEST_RATE_LIMIT_REQUESTS=3, GUEST_RATE_LIMIT_WINDOW=60)
class GuestRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_guest_gets_429_over_the_limit(self):
        client = Client(REMOTE_ADDR="203.0.113.7")
        for _ in range(3):
            self.assertEqual(client.get(reverse("steam_catalog:list")).status_code, 200)
        response = client.get(reverse("steam_catalog:list"))
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        # Another address and the pages outside the catalog are not affected.
        self.assertEqual(Client(REMOTE_ADDR="203.0.113.8").get(reverse("steam_catalog:list")).status_code, 200)
        self.assertEqual(client.get("/robots.txt").status_code, 200)

    def test_signed_in_users_are_not_limited(self):
        client = Client(REMOTE_ADDR="203.0.113.9")
        client.force_login(User.objects.create_user(username="fan", password="x"))
        for _ in range(5):
            self.assertEqual(client.get(reverse("steam_catalog:list")).status_code, 200)
