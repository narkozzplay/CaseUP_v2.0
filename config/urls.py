from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from modules.v2.referrals.views import referral_link


def healthcheck(_request):
    return HttpResponse("OK")


urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", healthcheck, name="healthcheck"),
    path("oauth/", include("social_django.urls", namespace="social_auth")),
    path("telegram/", include("modules.v2.tg_notifier.public_urls")),
    path("payments/", include("modules.v2.payments.public_urls")),
    path("r/<str:code>/", referral_link, name="referral_link"),
]

urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
    path("auth/", include("modules.v2.accounts.urls")),
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
    path("referrals/", include("modules.v2.referrals.urls")),
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
