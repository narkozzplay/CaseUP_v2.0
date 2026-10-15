"""robots.txt and sitemap.xml: only public pages are offered to search engines."""
from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

PUBLIC_PAGES = (
    ("frontend:home", "daily", 1.0),
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
        return reverse(item)

    def changefreq(self, item):
        return dict((name, freq) for name, freq, _p in PUBLIC_PAGES).get(item, "weekly")

    def priority(self, item):
        return dict((name, priority) for name, _f, priority in PUBLIC_PAGES).get(item, 0.5)


SITEMAPS = {"pages": PublicPagesSitemap}


@require_GET
@cache_page(60 * 60)
def robots_txt(request):
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]
    lines += ["", f"Sitemap: https://{request.get_host()}{reverse('sitemap')}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
