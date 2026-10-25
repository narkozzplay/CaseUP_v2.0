from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.subscriptions.models import SubscriptionFeature, SubscriptionPlan, UserSubscription

from .models import SteamItem, SteamItemFavorite
from .services import browse

User = get_user_model()


def skin(base: str, exterior: str, price: str | None, *, stattrak=False, rarity="covert", weapon=None, stock=0):
    name = f"{'StatTrak™ ' if stattrak else ''}{base} ({exterior})"
    return SteamItem.objects.create(
        market_hash_name=name, family_name=base, name=base, item_type="skin", exterior=exterior, is_stattrak=stattrak,
        wear_order={"Factory New": 1, "Field-Tested": 3, "Battle-Scarred": 5}[exterior], rarity_key=rarity,
        weapon=weapon or base.replace("★ ", "").split(" | ")[0], price_rub=Decimal(price) if price else None, stock=stock,
    )


class CatalogFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        skin("AK-47 | Redline", "Field-Tested", "1250", rarity="classified")
        skin("AK-47 | Redline", "Battle-Scarred", "900", rarity="classified")
        skin("AK-47 | Redline", "Field-Tested", "2600", stattrak=True, rarity="classified")
        skin("AWP | Asiimov", "Battle-Scarred", "7490")
        skin("★ Karambit | Fade", "Factory New", "98500")
        skin("★ Sport Gloves | Vice", "Field-Tested", "120000", weapon="Sport Gloves")
        skin("Glock-18 | Fade", "Factory New", None, rarity="restricted")
        SteamItem.objects.create(market_hash_name="Recoil Case", name="Recoil Case", item_type="case", price_rub=Decimal("42"))

    def cards(self, **params):
        f = browse.parse_filters(params)
        rows = browse.family_rows(f, fav_keys=[])
        return [card["family_name"] for card in browse.build_cards(rows, f, fav_keys=[])], rows

    def test_one_card_per_family_with_the_filtered_price_range(self):
        names, rows = self.cards(q="ak redline")
        self.assertEqual(names, ["AK-47 | Redline"])
        self.assertEqual((rows[0]["min_price"], rows[0]["max_price"], rows[0]["variants"]), (Decimal("900.00"), Decimal("2600.00"), 3))
        _names, rows = self.cards(q="redline", stattrak="0", exterior="Field-Tested")
        self.assertEqual((rows[0]["min_price"], rows[0]["variants"]), (Decimal("1250.00"), 1))

    def test_weapon_groups_rarity_price_and_sort(self):
        self.assertEqual(self.cards(group="knife")[0], ["★ Karambit | Fade"])
        self.assertEqual(self.cards(group="gloves")[0], ["★ Sport Gloves | Vice"])
        self.assertEqual(self.cards(group="rifle")[0], ["AK-47 | Redline"])
        self.assertEqual(self.cards(rarity="restricted")[0], ["Glock-18 | Fade"])
        self.assertEqual(self.cards(price_min="1000", price_max="8000", sort="price_asc")[0], ["AK-47 | Redline", "AWP | Asiimov"])
        self.assertEqual(self.cards(sort="price_desc", type="skin")[0][:2], ["★ Sport Gloves | Vice", "★ Karambit | Fade"])
        self.assertNotIn("Glock-18 | Fade", self.cards(priced="1")[0])
        self.assertEqual(self.cards(type="case")[0], ["Recoil Case"])

    def test_bad_input_is_ignored(self):
        f = browse.parse_filters({"type": "nope", "rarity": "x", "price_min": "abc", "sort": "drop table", "stock": "1"})
        self.assertEqual((f.type, f.rarity, f.price_min, f.sort, f.in_stock), ("", "", None, "updated", False))

    def test_weapon_options_are_grouped(self):
        options = browse.filter_options(browse.parse_filters({}))
        groups = {group["label"]: group["weapons"] for group in options["weapon_groups"]}
        self.assertEqual(groups["Ножи"], ["Karambit"])
        self.assertEqual(groups["Перчатки"], ["Sport Gloves"])
        self.assertIn("AK-47", groups["Винтовки"])


class CatalogFavoriteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fan", password="x")
        UserSubscription.objects.create(user=self.user, plan=SubscriptionPlan.objects.get(code="site_no_ads"))
        self.client = Client()
        self.client.force_login(self.user)
        self.old = skin("AWP | Asiimov", "Battle-Scarred", "7490")
        self.redline = skin("AK-47 | Redline", "Field-Tested", "1250")
        skin("AK-47 | Redline", "Battle-Scarred", "900")
        SteamItem.objects.filter(pk=self.old.pk).update(updated_at=self.old.updated_at.replace(year=2020))

    def test_star_pins_the_family_first_and_has_its_own_tab(self):
        page = self.client.get(reverse("steam_catalog:list"))
        self.assertLess(page.content.decode().index("AK-47 | Redline"), page.content.decode().index("AWP | Asiimov"))

        response = self.client.post(reverse("steam_catalog:favorite", args=[self.old.pk]), HTTP_ACCEPT="application/json")
        self.assertEqual(response.json(), {"favorite": True, "count": 1})
        page = self.client.get(reverse("steam_catalog:list")).content.decode()
        self.assertLess(page.index("AWP | Asiimov"), page.index("AK-47 | Redline"))

        only = self.client.get(reverse("steam_catalog:list"), {"fav": "1"}).content.decode()
        self.assertIn("AWP | Asiimov", only)
        self.assertNotIn("AK-47 | Redline", only)

    def test_any_variant_stars_the_whole_family_and_toggles_back(self):
        self.client.post(reverse("steam_catalog:favorite", args=[self.redline.pk]))
        other_wear = SteamItem.objects.get(market_hash_name="AK-47 | Redline (Battle-Scarred)")
        self.assertTrue(browse.is_favorite(self.user, other_wear))
        detail = self.client.get(reverse("steam_catalog:detail", args=[other_wear.pk]))
        self.assertContains(detail, 'aria-pressed="true"')
        response = self.client.post(reverse("steam_catalog:favorite", args=[other_wear.pk]), {"next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("steam_catalog:detail", args=[other_wear.pk]))
        self.assertFalse(SteamItemFavorite.objects.exists())

    def test_locked_catalog_cannot_star(self):
        SubscriptionFeature.objects.filter(code="steam_catalog_view").update(free_tier=SubscriptionFeature.FreeTier.NONE)
        plain = User.objects.create_user(username="plain", password="x")
        client = Client()
        client.force_login(plain)
        self.assertEqual(client.post(reverse("steam_catalog:favorite", args=[self.old.pk])).status_code, 403)
        self.assertEqual(client.get(reverse("steam_catalog:favorite", args=[self.old.pk])).status_code, 405)
