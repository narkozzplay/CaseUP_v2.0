from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .models import Notification
from .services import (
    build_notification_rows,
    get_notifications,
    mark_all_read,
    mark_read,
    render_notification,
    unread_count,
)


def safe_next_url(request, default: str) -> str:
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


@login_required
def notifications_list_view(request):
    show_unread = request.GET.get("filter") == "unread"
    lang = getattr(request, "LANGUAGE_CODE", "ru")
    rows = build_notification_rows(get_notifications(request.user, unread_only=show_unread), lang)
    return render_dashboard_page(
        request,
        full_template="notifications/list.html",
        partial_template="notifications/partials/list_content.html",
        context={
            "notification_rows": rows,
            "notifications_filter": "unread" if show_unread else "all",
            "notifications_page_unread": unread_count(request.user),
        },
        page_title=build_page_title("Уведомления" if str(lang).startswith("ru") else "Notifications"),
        active_menu_key="notifications",
    )


@login_required
def notification_open_view(request, notification_id: int):
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    mark_read(request.user, notification.pk)
    view = render_notification(notification, getattr(request, "LANGUAGE_CODE", "ru"))
    return redirect(view.url or "notifications:list")


@login_required
def notification_mark_read_view(request, notification_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    mark_read(request.user, notification_id)
    return redirect(safe_next_url(request, "notifications:list"))


@login_required
def notifications_mark_all_read_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    mark_all_read(request.user)
    return redirect(safe_next_url(request, "notifications:list"))


@require_GET
@login_required
def notifications_unread_count_view(request):
    return JsonResponse({"unread": unread_count(request.user)})
