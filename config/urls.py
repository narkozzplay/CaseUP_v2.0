from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path

from django.contrib.sitemaps import views as sitemap_views
from django.views.decorators.cache import cache_page

from frontend.seo import SITEMAPS, robots_txt
from modules.v2.referrals.views import referral_link
from modules.v2.tg_notifier import views as tg_notifier_views
from modules.v2.webpush import views as webpush_views


def healthcheck(_request):
    return HttpResponse("OK")


def readiness(_request):
    """For an external monitor (UptimeRobot): 200 only when the database, the cache and background tasks
    work; 503 otherwise. /health/ stays the cheap liveness check for the Docker healthcheck."""
    from modules.v2.control_center.services import health

    checks = [health.check_database(), health.check_cache(), health.check_scheduler()]
    failed = any(check.status == health.FAIL for check in checks)
    body = {"status": "fail" if failed else "ok", "checks": {check.key: check.status for check in checks}}
    return JsonResponse(body, status=503 if failed else 200, headers={"Cache-Control": "no-store"})



urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", healthcheck, name="healthcheck"),
    path("health/ready/", readiness, name="readiness"),
    path("oauth/", include("social_django.urls", namespace="social_auth")),
    path("telegram/", include("modules.v2.tg_notifier.public_urls")),
    path("payments/", include("modules.v2.payments.public_urls")),
    path("r/<str:code>/", referral_link, name="referral_link"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sw.js", webpush_views.service_worker, name="service_worker"),
    path("manifest.webmanifest", webpush_views.manifest, name="web_manifest"),
    path("sitemap.xml", cache_page(60 * 60)(sitemap_views.index), {"sitemaps": SITEMAPS, "sitemap_url_name": "sitemap"}, name="sitemap_index"),
    path("sitemap-<section>.xml", cache_page(60 * 60 * 6)(sitemap_views.sitemap), {"sitemaps": SITEMAPS}, name="sitemap"),
]

urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
    path("auth/", include("modules.v2.accounts.urls")),
    path("steam-catalog/top/", tg_notifier_views.price_movers_page, name="price_movers"),
    path("steam-catalog/", include("modules.v2.steam_catalog.urls")),
    path("farm/", include("modules.v2.farm.urls")),
    path("tradehub/", include("modules.v2.tradehub.urls")),
    path("steam-trading/", include("modules.v2.steam_trading.urls")),
    path("control-center/system/", include("modules.v2.system.urls")),
    path("control-center/celery-tasks/", include("modules.v2.celery_tasks.urls")),
    path("control-center/tg-notifier/", include("modules.v2.tg_notifier.urls")),
    path("control-center/", include("modules.v2.control_center.urls")),
    path("subscriptions/", include("modules.v2.subscriptions.urls")),
    path("servers/", include("modules.v2.game_servers.urls")),
    path("cases/", include("modules.v2.case_simulator.urls")),
    path("payments/", include("modules.v2.payments.urls")),
    path("store/", include("modules.v2.store.urls")),
    path("constructor/", include("modules.v2.web_constructor.urls")),
    path("social/", include("modules.v2.social.urls")),
    path("social/", include("modules.v2.social_friends.urls")),
    path("notifications/", include("modules.v2.notifications.urls")),
    path("support/", include("modules.v2.support.urls")),
    path("referrals/", include("modules.v2.referrals.urls")),
    path("promo/", include("modules.v2.promo.urls")),
    path("bonus/", include("modules.v2.daily_bonus.urls")),
    path("donate/", include("modules.v2.donations.urls")),
    path("ads/", include("modules.v2.ads.urls")),
    path("market/", include("modules.v2.market.urls")),
    path("push/", include("modules.v2.webpush.urls")),
    path("customization/", include("modules.v2.profile_customization.urls")),
    path("cs2/stats/", include("modules.v2.cs2_stats.urls")),
    path("", include("frontend.urls")),
    path("", include("modules.v2.profiles.urls")),
    prefix_default_language=False,
)


handler404 = "frontend.views.not_found_page"
handler403 = "frontend.views.permission_denied_page"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
