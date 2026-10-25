"""robots.txt and sitemap.xml: only public pages are offered to search engines."""
from __future__ import annotations

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.urls import NoReverseMatch, reverse
from django.utils import translation
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

PUBLIC_PAGES = (
    ("frontend:home", "daily", 1.0),
    ("steam_catalog:list", "daily", 0.9),
    ("price_movers", "daily", 0.8),
    ("subscriptions:plans", "weekly", 0.8),
    ("game_servers:list", "daily", 0.7),
    ("frontend:agreement", "monthly", 0.3),
    ("frontend:privacy", "monthly", 0.3),
    ("frontend:pd_consent", "monthly", 0.2),
    ("frontend:cookies", "monthly", 0.2),
)

PRIVATE_PREFIXES = (
    "/admin/", "/auth/", "/control-center/", "/oauth/", "/r/", "/promo/", "/referrals/", "/payments/",
    "/notifications/", "/social/", "/customization/", "/telegram/", "/i18n/", "/profile/", "/en/",
)


class PublicPagesSitemap(Sitemap):
    protocol = "https"

    def items(self):
        names = []
        for name, _freq, _priority in PUBLIC_PAGES:
            try:
                reverse(name)
            except NoReverseMatch:
                continue
            names.append(name)
        return names

    def location(self, item):
        # Russian addresses have no language prefix; /en/ is closed in robots.txt.
        with translation.override(settings.LANGUAGE_CODE):
            return reverse(item)

    def changefreq(self, item):
        return dict((name, freq) for name, freq, _p in PUBLIC_PAGES).get(item, "weekly")

    def priority(self, item):
        return dict((name, priority) for name, _f, priority in PUBLIC_PAGES).get(item, 0.5)


class CatalogItemsSitemap(Sitemap):
    """Every active catalog item that has a price: these pages answer searches like "AK-47 Redline price"."""

    protocol = "https"
    changefreq = "daily"
    priority = 0.6
    limit = 10000

    def items(self):
        from modules.v2.steam_catalog.models import SteamItem

        return (
            SteamItem.objects.filter(is_active=True, price_rub__isnull=False)
            .only("id", "market_hash_name", "updated_at")
            .order_by("id")
        )

    def location(self, item):
        with translation.override(settings.LANGUAGE_CODE):
            return item.get_absolute_url()

    def lastmod(self, item):
        return item.updated_at


SITEMAPS = {"pages": PublicPagesSitemap, "items": CatalogItemsSitemap}


@require_GET
@cache_page(60 * 60)
def robots_txt(request):
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]
    lines += ["", f"Sitemap: https://{request.get_host()}{reverse('sitemap_index')}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
