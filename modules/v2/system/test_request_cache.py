from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from modules.v2.system.models import SiteConfiguration
from modules.v2.system.request_cache import RequestCacheMiddleware
from modules.v2.system.services import get_module_state_map, get_site_configuration


def _in_request(func):
    """Run func inside the request-scoped cache, the way RequestCacheMiddleware does for views."""
    middleware = RequestCacheMiddleware(lambda _request: func())
    return middleware(RequestFactory().get("/"))


class RequestCacheTests(TestCase):
    def setUp(self):
        get_site_configuration()  # make sure the singleton exists

    def test_lookups_are_memoized_within_a_request(self):
        def body():
            with CaptureQueriesContext(connection) as ctx:
                for _ in range(5):
                    get_site_configuration()
            return len(ctx.captured_queries)

        self.assertEqual(_in_request(body), 1)

    def test_no_memoization_outside_a_request(self):
        with CaptureQueriesContext(connection) as ctx:
            get_site_configuration()
            get_site_configuration()
        self.assertEqual(len(ctx.captured_queries), 2)

    def test_save_during_request_drops_cached_values(self):
        def body():
            config = get_site_configuration()
            config.site_name = "Renamed in request"
            config.save()
            return get_site_configuration().site_name

        self.assertEqual(_in_request(body), "Renamed in request")

    def test_callers_get_independent_copies(self):
        def body():
            first = get_site_configuration()
            first.site_name = "mutated, not saved"
            state_map = get_module_state_map()
            state_map["store"] = "mutated"
            return get_site_configuration().site_name, get_module_state_map().get("store")

        site_name, store_state = _in_request(body)
        self.assertNotEqual(site_name, "mutated, not saved")
        self.assertNotEqual(store_state, "mutated")
        self.assertEqual(SiteConfiguration.objects.count(), 1)


class PageQueryBudgetTests(TestCase):
    """Pages issued ~90-100 queries before per-request memoization; keep them well below that."""

    BUDGET = 55

    def test_dashboard_pages_stay_within_query_budget(self):
        user = get_user_model().objects.create_user(username="query_budget", password="StrongPass123")
        self.client.force_login(user)
        for url in ("/", "/dashboard-preview/", "/profile/", "/cases/", "/notifications/", "/subscriptions/"):
            self.client.get(url)  # warm one-time bootstrap work
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertLessEqual(len(ctx.captured_queries), self.BUDGET, f"{url}: {len(ctx.captured_queries)} queries")
