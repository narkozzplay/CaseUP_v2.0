from decimal import ROUND_HALF_UP, Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile

from . import services
from .models import SimCase, SimCaseItem, SimOpening, TerminalSession


def _make_case(*, slug="test-case", kind=SimCase.Kind.CUSTOM, case_price="100", key_price="50"):
    case = SimCase.objects.create(slug=slug, name=slug.title(), kind=kind, case_price=Decimal(case_price), key_price=Decimal(key_price))
    rows = [
        ("milspec", 3, "20"),
        ("restricted", 2, "150"),
        ("classified", 1, "700"),
        ("covert", 1, "3000"),
        ("gold", 1, "20000"),
    ]
    for rarity, count, value in rows:
        for index in range(count):
            SimCaseItem.objects.create(
                case=case,
                name=f"AK-47 | {rarity} {index}",
                weapon="AK-47",
                rarity=rarity,
                value=Decimal(value),
                min_float=Decimal("0"),
                max_float=Decimal("1"),
            )
    return case


class CaseSimulatorServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="opener", password="pass12345")
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.balance_coins = Decimal("1000.00")
        self.profile.save()
        self.case = _make_case()

    def test_seed_migration_loaded_default_cases(self):
        kinds = set(SimCase.objects.exclude(slug="test-case").values_list("kind", flat=True))
        self.assertEqual(kinds, {SimCase.Kind.OFFICIAL, SimCase.Kind.CUSTOM, SimCase.Kind.TERMINAL})
        self.assertEqual(SimCase.objects.filter(kind=SimCase.Kind.CUSTOM).exclude(slug="test-case").count(), 10)
        self.assertTrue(SimCase.objects.get(slug="kilowatt").has_key)

    def test_custom_cases_use_bundled_covers(self):
        for case in SimCase.objects.filter(kind__in=[SimCase.Kind.CUSTOM, SimCase.Kind.TERMINAL]).exclude(slug="test-case"):
            with self.subTest(slug=case.slug):
                self.assertTrue(case.cover_url.endswith(f"img/cases/{case.slug}.webp"))
        self.assertEqual(self.case.cover_url, "")

    def test_rarity_odds_follow_cs2_tiers(self):
        summary = {row["rarity"]: row["chance"] for row in services.rarity_summary(self.case)}
        self.assertEqual(summary["milspec"], Decimal("79.92"))
        self.assertEqual(summary["restricted"], Decimal("15.98"))
        self.assertEqual(summary["covert"], Decimal("0.64"))
        self.assertEqual(summary["gold"], Decimal("0.26"))
        weights = services.item_weights(list(self.case.items.all()))
        self.assertAlmostEqual(float(sum(weight for _, weight in weights)), 1.0, places=6)

    def test_custom_weight_overrides_tier_odds(self):
        gold = self.case.items.get(rarity="gold")
        gold.weight = Decimal("100")
        gold.save()
        chances = {row["item"].pk: row["chance"] for row in services.drop_chances(self.case)}
        self.assertGreater(chances[gold.pk], Decimal("98"))

    def test_open_case_charges_case_and_key(self):
        openings = services.open_case(user=self.user, case=self.case, count=2)
        self.assertEqual(len(openings), 2)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance_coins, Decimal("700.00"))
        entry = LedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.reason, LedgerReason.CASE_OPEN)
        self.assertEqual(entry.delta, Decimal("-300.00"))
        self.assertTrue(all(opening.price_paid == Decimal("150.00") for opening in openings))

    def test_open_case_without_balance_fails_and_saves_nothing(self):
        self.profile.balance_coins = Decimal("100.00")
        self.profile.save()
        with self.assertRaises(services.SimulatorError):
            services.open_case(user=self.user, case=self.case)
        self.assertFalse(SimOpening.objects.exists())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance_coins, Decimal("100.00"))

    def test_roll_sets_exterior_from_float(self):
        item = self.case.items.first()
        with mock.patch.object(services._rng, "uniform", return_value=0.05), mock.patch.object(services._rng, "random", return_value=0.9):
            roll = services.roll_item(item)
        self.assertEqual(roll.exterior, "Factory New")
        self.assertFalse(roll.is_stattrak)
        self.assertEqual(roll.value, Decimal("26.00"))

    def test_strip_places_winner_at_fixed_index(self):
        winner = self.case.items.get(rarity="gold")
        strip = services.build_strip(self.case, winner)
        self.assertEqual(len(strip), services.STRIP_LENGTH)
        self.assertEqual(strip[services.STRIP_WIN_INDEX], winner)


class TerminalServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="trader", password="pass12345")
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.balance_coins = Decimal("100000.00")
        self.profile.save()
        self.terminal = _make_case(slug="term", kind=SimCase.Kind.TERMINAL, case_price="80", key_price="0")

    def test_start_charges_access_and_creates_offers_with_markup(self):
        session = services.start_terminal(user=self.user, case=self.terminal)
        self.assertEqual(len(session.offers), 5)
        for offer in session.offers:
            self.assertEqual(Decimal(offer["price"]), (Decimal(offer["value"]) * Decimal("1.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance_coins, Decimal("99920.00"))
        self.assertEqual(services.start_terminal(user=self.user, case=self.terminal).pk, session.pk)

    def test_accept_pays_offer_and_closes_terminal(self):
        session = services.start_terminal(user=self.user, case=self.terminal)
        services.decline_terminal_offer(user=self.user, session_id=session.pk)
        price = Decimal(session.offers[1]["price"])
        opening = services.accept_terminal_offer(user=self.user, session_id=session.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, TerminalSession.Status.ACCEPTED)
        self.assertEqual(session.opening, opening)
        self.assertEqual(opening.source, SimOpening.Source.TERMINAL)
        self.assertEqual(opening.price_paid, price)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance_coins, Decimal("99920.00") - price)
        with self.assertRaises(services.SimulatorError):
            services.decline_terminal_offer(user=self.user, session_id=session.pk)

    def test_declining_every_offer_burns_terminal(self):
        session = services.start_terminal(user=self.user, case=self.terminal)
        for _ in range(5):
            session = services.decline_terminal_offer(user=self.user, session_id=session.pk)
        self.assertEqual(session.status, TerminalSession.Status.BURNED)
        self.assertFalse(SimOpening.objects.exists())

    def test_terminal_cannot_be_opened_as_case(self):
        with self.assertRaises(services.SimulatorError):
            services.open_case(user=self.user, case=self.terminal)


class CaseSimulatorViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="viewer", password="pass12345")
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.balance_coins = Decimal("1000.00")
        self.profile.save()
        self.client.force_login(self.user)
        self.case = _make_case()

    def test_pages_render(self):
        for url in (
            reverse("case_simulator:list"),
            reverse("case_simulator:detail", args=["test-case"]),
            reverse("case_simulator:detail", args=["kilowatt"]),
            reverse("case_simulator:terminal", args=["genesis-uplink"]),
            reverse("case_simulator:inventory"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_pages_require_login(self):
        self.client.logout()
        response = self.client.get(reverse("case_simulator:list"))
        self.assertEqual(response.status_code, 302)

    def test_open_endpoint_returns_strips_and_balance(self):
        response = self.client.post(reverse("case_simulator:open", args=["test-case"]), {"count": "3"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(len(payload["strips"]), 3)
        for result, strip in zip(payload["results"], payload["strips"]):
            self.assertEqual(strip[payload["win_index"]]["id"], result["id"])
        self.assertEqual(payload["balance"], "550.00")
        self.assertEqual(SimOpening.objects.filter(user=self.user).count(), 3)

    def test_demo_open_is_free(self):
        response = self.client.post(reverse("case_simulator:open", args=["test-case"]), {"demo": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["demo"])
        self.assertFalse(SimOpening.objects.exists())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance_coins, Decimal("1000.00"))

    def test_open_endpoint_reports_low_balance(self):
        self.profile.balance_coins = Decimal("10.00")
        self.profile.save()
        response = self.client.post(reverse("case_simulator:open", args=["test-case"]))
        self.assertEqual(response.status_code, 402)
        self.assertFalse(response.json()["ok"])

    def test_open_endpoint_rejects_get(self):
        self.assertEqual(self.client.get(reverse("case_simulator:open", args=["test-case"])).status_code, 405)

    def test_terminal_endpoints_flow(self):
        response = self.client.post(reverse("case_simulator:terminal_start", args=["genesis-uplink"]))
        self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["offer"]["index"], 1)
        session_id = state["session"]

        state = self.client.post(reverse("case_simulator:terminal_decline", args=[session_id])).json()
        self.assertEqual(state["offer"]["index"], 2)

        other = get_user_model().objects.create_user(username="other", password="pass12345")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("case_simulator:terminal_accept", args=[session_id])).status_code, 400)
        self.client.force_login(self.user)

        self.profile.balance_coins = Decimal("100000.00")
        self.profile.save()
        state = self.client.post(reverse("case_simulator:terminal_accept", args=[session_id])).json()
        self.assertEqual(state["status"], "accepted")
        self.assertIn("result", state)


class InventorySellTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="seller", password="pass12345")
        self.other = get_user_model().objects.create_user(username="other", password="pass12345")
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.balance_coins = Decimal("10.00")
        self.profile.save()
        self.client.force_login(self.user)
        self.case = _make_case()
        items = list(self.case.items.order_by("id"))
        self.cheap = self._drop(self.user, items[0], "20.50")
        self.mid = self._drop(self.user, items[3], "150.00")
        self.top = self._drop(self.user, items[-1], "20000.00")
        self.foreign = self._drop(self.other, items[0], "20.00")

    def _drop(self, user, item, value):
        return SimOpening.objects.create(user=user, case=self.case, item=item, price_paid=Decimal("150"), item_value=Decimal(value))

    def _balance(self):
        self.profile.refresh_from_db()
        return self.profile.balance_coins

    def test_sell_single_credits_value_and_hides_item(self):
        response = self.client.post(reverse("case_simulator:inventory_sell"), {"sell": self.cheap.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._balance(), Decimal("30.50"))
        self.cheap.refresh_from_db()
        self.assertTrue(self.cheap.is_sold)
        self.assertEqual(self.cheap.sold_price, Decimal("20.50"))
        entry = LedgerEntry.objects.get(user=self.user, reason=LedgerReason.ITEM_SALE)
        self.assertEqual((entry.delta, entry.case_opening_id), (Decimal("20.50"), self.cheap.pk))

        page = self.client.get(reverse("case_simulator:inventory"))
        self.assertNotIn(self.cheap, page.context["openings"])
        self.assertEqual(page.context["stats"]["inventory_count"], 2)
        self.assertEqual(page.context["stats"]["sold_value"], Decimal("20.50"))

    def test_sell_selected_ignores_foreign_and_sold_items(self):
        self.client.post(reverse("case_simulator:inventory_sell"), {"sell": self.cheap.pk})
        self.client.post(
            reverse("case_simulator:inventory_sell"),
            {"action": "selected", "ids": [self.cheap.pk, self.mid.pk, self.foreign.pk]},
        )
        self.assertEqual(self._balance(), Decimal("180.50"))
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_sold)
        self.assertEqual(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.ITEM_SALE).count(), 2)

    def test_sell_all_respects_rarity_filter(self):
        self.client.post(reverse("case_simulator:inventory_sell"), {"action": "all", "rarity": "gold"})
        self.assertEqual(self._balance(), Decimal("20010.00"))
        self.client.post(reverse("case_simulator:inventory_sell"), {"action": "all"})
        self.assertEqual(self._balance(), Decimal("20180.50"))
        self.assertFalse(SimOpening.objects.filter(user=self.user, sold_at__isnull=True).exists())

    def test_selling_nothing_reports_error(self):
        response = self.client.post(reverse("case_simulator:inventory_sell"), {"action": "selected"}, follow=True)
        self.assertContains(response, "Нечего продавать")
        self.assertEqual(self._balance(), Decimal("10.00"))

    def test_best_drop_shows_image(self):
        self.top.item.image_url = "https://example.com/gold.png"
        self.top.item.save()
        response = self.client.get(reverse("case_simulator:inventory"))
        self.assertContains(response, 'class="sim-best__media"')
        self.assertContains(response, "https://example.com/gold.png")

    def test_json_sell_from_drop_dialog(self):
        response = self.client.post(
            reverse("case_simulator:inventory_sell"),
            {"ids": [self.cheap.pk, self.mid.pk]},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.json(), {"ok": True, "sold": 2, "total": "170.50", "balance": "180.50"})
        again = self.client.post(reverse("case_simulator:inventory_sell"), {"ids": [self.cheap.pk]}, HTTP_ACCEPT="application/json")
        self.assertEqual(again.status_code, 400)
        self.assertFalse(again.json()["ok"])


class ControlCenterCaseTests(TestCase):
    def setUp(self):
        from modules.v2.profiles.models import UserRole

        self.admin = get_user_model().objects.create_user(username="cc_admin", password="pass12345")
        profile, _ = UserProfile.objects.get_or_create(user=self.admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        self.client.login(username="cc_admin", password="pass12345")
        self.case = _make_case(slug="cc-case")

    def _post_data(self, case, **overrides):
        items = list(case.items.all()) if case else []
        data = {
            "name": case.name if case else "Новый",
            "slug": case.slug if case else "new-case",
            "kind": case.kind if case else SimCase.Kind.CUSTOM,
            "collection_name": "",
            "description": "",
            "case_price": str(case.case_price) if case else "50",
            "key_price": str(case.key_price) if case else "0",
            "image_url": "",
            "accent_color": "#f5a623",
            "terminal_offers": "5",
            "terminal_markup_percent": "10",
            "is_active": "on",
            "sort_order": "100",
            "items-TOTAL_FORMS": str(len(items) + 1),
            "items-INITIAL_FORMS": str(len(items)),
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, item in enumerate(items):
            prefix = f"items-{index}-"
            data.update({
                prefix + "id": str(item.pk),
                prefix + "name": item.name,
                prefix + "weapon": item.weapon,
                prefix + "rarity": item.rarity,
                prefix + "value": str(item.value),
                prefix + "weight": "",
                prefix + "min_float": str(item.min_float),
                prefix + "max_float": str(item.max_float),
                prefix + "stattrak_allowed": "on",
                prefix + "image_url": "",
                prefix + "sort_order": "100",
            })
        # A blank extra row as the browser sends it, with the model defaults pre-filled.
        blank = f"items-{len(items)}-"
        data.update({blank + "rarity": "milspec", blank + "value": "0.00", blank + "sort_order": "100", blank + "stattrak_allowed": "on"})
        data.update(overrides)
        return data

    def test_pages_render(self):
        response = self.client.get(reverse("control_center:cases"))
        self.assertContains(response, "cc-case")
        self.assertContains(self.client.get(reverse("control_center:case_edit", args=[self.case.pk])), "Шансы выпадения")
        self.assertEqual(self.client.get(reverse("control_center:case_create")).status_code, 200)

    def test_forbidden_for_regular_user(self):
        get_user_model().objects.create_user(username="plain_cc", password="pass12345")
        self.client.login(username="plain_cc", password="pass12345")
        self.assertEqual(self.client.get(reverse("control_center:cases")).status_code, 403)
        self.assertEqual(self.client.get(reverse("control_center:case_edit", args=[self.case.pk])).status_code, 403)

    def test_create_case_with_items(self):
        data = self._post_data(None, **{
            "items-0-name": "M4A4 | Test",
            "items-0-rarity": "covert",
            "items-0-value": "1000",
            "items-0-weight": "2",
            "items-0-sort_order": "100",
        })
        response = self.client.post(reverse("control_center:case_create"), data)
        case = SimCase.objects.get(slug="new-case")
        self.assertRedirects(response, reverse("control_center:case_edit", args=[case.pk]), fetch_redirect_response=False)
        self.assertEqual(list(case.items.values_list("name", flat=True)), ["M4A4 | Test"])

    def test_edit_price_and_add_item(self):
        count = self.case.items.count()
        data = self._post_data(self.case, case_price="77.50", **{
            f"items-{count}-name": "Glock-18 | New",
            f"items-{count}-rarity": "milspec",
            f"items-{count}-value": "5",
            f"items-{count}-sort_order": "100",
        })
        self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.case.refresh_from_db()
        self.assertEqual(self.case.case_price, Decimal("77.50"))
        self.assertEqual(self.case.items.count(), count + 1)
        from modules.v2.control_center.models import AdminAuditLog, AuditAction

        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.CASE_SIM_UPDATE).exists())

    def test_item_with_openings_cannot_be_deleted(self):
        item = self.case.items.first()
        SimOpening.objects.create(user=self.admin, case=self.case, item=item)
        data = self._post_data(self.case, **{"items-0-DELETE": "on"})
        response = self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже выпадал игрокам, его нельзя удалить")
        self.assertTrue(SimCaseItem.objects.filter(pk=item.pk).exists())

    def test_float_range_validated(self):
        data = self._post_data(self.case, **{"items-0-min_float": "0.9", "items-0-max_float": "0.1"})
        response = self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "мин ≤ макс")

    def test_toggle_and_delete(self):
        self.client.post(reverse("control_center:cases"), {"case_id": self.case.pk, "action": "toggle"})
        self.case.refresh_from_db()
        self.assertFalse(self.case.is_active)
        SimOpening.objects.create(user=self.admin, case=self.case, item=self.case.items.first())
        self.client.post(reverse("control_center:cases"), {"case_id": self.case.pk, "action": "delete"})
        self.assertTrue(SimCase.objects.filter(pk=self.case.pk).exists())
        empty = SimCase.objects.create(slug="empty-cc", name="Empty")
        self.client.post(reverse("control_center:cases"), {"case_id": empty.pk, "action": "delete"})
        self.assertFalse(SimCase.objects.filter(pk=empty.pk).exists())

    def test_negative_price_rejected(self):
        data = self._post_data(self.case, case_price="-10")
        response = self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.assertContains(response, "не может быть отрицательной")
        self.case.refresh_from_db()
        self.assertGreater(self.case.case_price, 0)

    def test_item_in_active_terminal_offer_cannot_be_deleted(self):
        item = self.case.items.first()
        TerminalSession.objects.create(user=self.admin, case=self.case, offers=[{"item_id": item.pk}])
        data = self._post_data(self.case, **{"items-0-DELETE": "on"})
        response = self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.assertContains(response, "открытого терминала")
        self.assertTrue(SimCaseItem.objects.filter(pk=item.pk).exists())

    def test_markup_limit_enforced(self):
        data = self._post_data(self.case, terminal_markup_percent="1000")
        response = self.client.post(reverse("control_center:case_edit", args=[self.case.pk]), data)
        self.assertContains(response, "Не больше 500%")

    def test_empty_case_cannot_be_enabled(self):
        empty = SimCase.objects.create(slug="empty-off", name="Empty", is_active=False)
        self.client.post(reverse("control_center:cases"), {"case_id": empty.pk, "action": "toggle"})
        empty.refresh_from_db()
        self.assertFalse(empty.is_active)

    def test_terminal_kind_locked_while_sessions_active(self):
        terminal = _make_case(slug="cc-terminal", kind=SimCase.Kind.TERMINAL, key_price="0")
        TerminalSession.objects.create(user=self.admin, case=terminal, offers=[])
        data = self._post_data(terminal, kind=SimCase.Kind.CUSTOM)
        response = self.client.post(reverse("control_center:case_edit", args=[terminal.pk]), data)
        self.assertContains(response, "незавершённые сессии терминала")
        terminal.refresh_from_db()
        self.assertEqual(terminal.kind, SimCase.Kind.TERMINAL)

    def test_switched_off_terminal_reachable_with_active_session(self):
        terminal = _make_case(slug="cc-term-off", kind=SimCase.Kind.TERMINAL, key_price="0")
        TerminalSession.objects.create(user=self.admin, case=terminal, offers=[])
        terminal.is_active = False
        terminal.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(reverse("case_simulator:terminal", args=[terminal.slug])).status_code, 200)
        other = get_user_model().objects.create_user(username="no_session", password="pass12345")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("case_simulator:terminal", args=[terminal.slug])).status_code, 404)
