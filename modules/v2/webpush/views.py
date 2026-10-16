from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST

from . import services


def _json_body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}


@require_GET
@cache_control(no_cache=True, max_age=0)
def service_worker(request):
    response = HttpResponse(render_to_string("webpush/sw.js", request=request), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
@cache_control(max_age=3600)
def manifest(request):
    from modules.v2.system.services import get_site_configuration

    name = (get_site_configuration().site_name or "CaseUP").strip()
    data = {
        "name": name,
        "short_name": name[:12],
        "description": "Панель трейдера CS2: каталог цен, рынок с гарантом, кейсы и уведомления о сделках.",
        "lang": "ru",
        "start_url": reverse("frontend:dashboard_preview") + "?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0b0e13",
        "theme_color": "#0b0e13",
        "icons": [
            {"src": staticfiles_storage.url("img/brand/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": staticfiles_storage.url("img/brand/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": staticfiles_storage.url("img/brand/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})


@login_required
@require_GET
def public_key(request):
    return JsonResponse({"publicKey": services.public_key()})


@login_required
@require_POST
def subscribe(request):
    try:
        services.subscribe(request.user, _json_body(request), user_agent=request.META.get("HTTP_USER_AGENT", ""))
    except services.PushError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def unsubscribe(request):
    endpoint = str(_json_body(request).get("endpoint") or request.POST.get("endpoint") or "")
    removed = services.unsubscribe(request.user, endpoint)
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"ok": removed})
    messages.success(request, "Устройство отключено от уведомлений.")
    return redirect(reverse("profiles:profile_settings") + "#settings-push")


@login_required
@require_POST
def send_test(request):
    sent = services.send_test(request.user)
    return JsonResponse({"sent": sent})


@login_required
@require_POST
def save_categories(request):
    services.save_categories(request.user, request.POST.getlist("category"))
    messages.success(request, "Настройки уведомлений сохранены.")
    return redirect(reverse("profiles:profile_settings") + "#settings-push")


@login_required
@require_POST
def dismiss_prompt(request):
    services.dismiss_prompt(request.user)
    return JsonResponse({"ok": True})
