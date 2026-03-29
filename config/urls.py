from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def healthcheck(_request):
    return HttpResponse("OK")


urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", healthcheck, name="healthcheck"),
    path("oauth/", include("social_django.urls", namespace="social")),
    path("telegram/", include("modules.v2.tg_notifier.public_urls")),
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
    path("subscriptions/", include("modules.v2.subscriptions.urls")),
    path("servers/", include("modules.v2.game_servers.urls")),
    path("", include("frontend.urls")),
    path("", include("modules.v2.profiles.urls")),
    prefix_default_language=False,
)


handler404 = "frontend.views.not_found_page"
