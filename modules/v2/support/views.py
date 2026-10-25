from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from modules.v2.profiles.presentation import get_user_avatar_url, get_user_display_name
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from . import services
from .models import SupportAttachment, SupportTicket
from .services import SupportError, is_support_staff

PAGE_SIZE = 25


def _ticket_for(request, ticket_id: int) -> SupportTicket:
    ticket = get_object_or_404(SupportTicket.objects.select_related("user", "user__profile_v2", "assignee", "assignee__profile_v2"), pk=ticket_id)
    if not services.can_view_ticket(request.user, ticket):
        raise Http404
    return ticket


def _staff_only(request) -> None:
    if not is_support_staff(request.user):
        raise Http404


def _person(user) -> dict:
    return {"name": get_user_display_name(user), "avatar_url": get_user_avatar_url(user)} if user else {"name": "—", "avatar_url": ""}


def _referer_path(request) -> str:
    raw = request.GET.get("from") or ""
    if raw and url_has_allowed_host_and_scheme(raw, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return raw[:300]
    return ""


@login_required
def ticket_list(request):
    tickets = SupportTicket.objects.filter(user=request.user).select_related("assignee", "assignee__profile_v2")
    page_obj = Paginator(tickets, PAGE_SIZE).get_page(request.GET.get("page"))
    return render_dashboard_page(
        request,
        full_template="support/list.html",
        partial_template="support/partials/list_content.html",
        context={
            "page_obj": page_obj,
            "active_count": tickets.filter(status__in=SupportTicket.ACTIVE_STATUSES).count(),
            "is_staff": is_support_staff(request.user),
            "queue_count": services.staff_queue_count(request.user),
        },
        page_title=build_page_title("Поддержка"),
        active_menu_key="support",
    )


@login_required
def ticket_create(request):
    values = {"subject": "", "category": SupportTicket.Category.OTHER, "body": "", "page_url": _referer_path(request)}
    if request.method == "POST":
        values = {key: (request.POST.get(key) or "").strip() for key in values}
        try:
            ticket = services.create_ticket(
                user=request.user,
                subject=values["subject"],
                category=values["category"],
                body=values["body"],
                files=request.FILES.getlist("attachments"),
                page_url=values["page_url"],
            )
        except SupportError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Обращение #{ticket.pk} создано. Мы ответим здесь и пришлём уведомление.")
            return redirect("support:detail", ticket_id=ticket.pk)
    return render_dashboard_page(
        request,
        full_template="support/create.html",
        partial_template="support/partials/create_content.html",
        context={
            "values": values,
            "categories": SupportTicket.Category.choices,
            "accept": services.ACCEPT_ATTRIBUTE,
            "max_attachments": services.MAX_ATTACHMENTS,
            "max_mb": services.MAX_ATTACHMENT_BYTES // (1024 * 1024),
        },
        page_title=build_page_title("Новое обращение", "Поддержка"),
        active_menu_key="support",
    )


@login_required
def ticket_detail(request, ticket_id: int):
    ticket = _ticket_for(request, ticket_id)
    staff_view = is_support_staff(request.user) and ticket.user_id != request.user.pk
    if ticket.user_id == request.user.pk:
        services.mark_seen_by_user(ticket)
    elif staff_view:
        from modules.v2.notifications.services import resolve_notifications

        from .notifications import KIND_STAFF

        resolve_notifications(KIND_STAFF, ticket.pk, recipient=request.user)

    rows = [
        {"message": message, "author": _person(message.author), "is_mine": message.author_id == request.user.pk}
        for message in services.visible_messages(ticket, staff=staff_view)
    ]
    context = {
        "ticket": ticket,
        "rows": rows,
        "staff_view": staff_view,
        "requester": _person(ticket.user),
        "assignee": _person(ticket.assignee) if ticket.assignee_id else None,
        "accept": services.ACCEPT_ATTRIBUTE,
        "max_attachments": services.MAX_ATTACHMENTS,
        "max_mb": services.MAX_ATTACHMENT_BYTES // (1024 * 1024),
        "statuses": SupportTicket.Status.choices,
        "priorities": SupportTicket.Priority.choices,
    }
    if staff_view:
        context["other_tickets"] = (
            SupportTicket.objects.filter(user=ticket.user).exclude(pk=ticket.pk).order_by("-last_message_at")[:5]
        )
    return render_dashboard_page(
        request,
        full_template="support/detail.html",
        partial_template="support/partials/detail_content.html",
        context=context,
        page_title=build_page_title(f"#{ticket.pk} {ticket.subject}", "Поддержка"),
        active_menu_key="support_queue" if staff_view else "support",
    )


@login_required
@require_POST
def ticket_reply(request, ticket_id: int):
    ticket = _ticket_for(request, ticket_id)
    internal = request.POST.get("internal") == "1"
    try:
        services.add_reply(
            ticket=ticket,
            author=request.user,
            body=request.POST.get("body") or "",
            files=request.FILES.getlist("attachments"),
            internal=internal,
        )
    except SupportError as exc:
        messages.error(request, str(exc))
    else:
        if internal:
            messages.success(request, "Заметка сохранена, пользователь её не видит.")
        elif ticket.user_id != request.user.pk:
            messages.success(request, "Ответ отправлен, пользователь получит уведомление.")
    return redirect(reverse("support:detail", args=[ticket.pk]) + "#reply")


@login_required
@require_POST
def ticket_close(request, ticket_id: int):
    ticket = _ticket_for(request, ticket_id)
    try:
        services.close_by_user(ticket=ticket, user=request.user)
    except SupportError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Обращение закрыто. Спасибо!")
    return redirect("support:detail", ticket_id=ticket.pk)


@login_required
@require_POST
def ticket_take(request, ticket_id: int):
    _staff_only(request)
    ticket = _ticket_for(request, ticket_id)
    services.take_ticket(ticket=ticket, moderator=request.user)
    messages.success(request, f"Обращение #{ticket.pk} теперь ваше.")
    return redirect("support:detail", ticket_id=ticket.pk)


@login_required
@require_POST
def ticket_manage(request, ticket_id: int):
    _staff_only(request)
    ticket = _ticket_for(request, ticket_id)
    try:
        priority = request.POST.get("priority")
        if priority:
            services.set_priority(ticket=ticket, actor=request.user, priority=priority)
        status = request.POST.get("status")
        if status:
            services.set_status(ticket=ticket, actor=request.user, status=status)
    except SupportError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Обращение обновлено.")
    return redirect("support:detail", ticket_id=ticket.pk)


@login_required
def staff_queue(request):
    _staff_only(request)
    scopes = dict(services.QUEUE_SCOPES)
    scope = request.GET.get("scope") or "new"
    if scope not in scopes:
        scope = "new"
    filters = {"scope": scope, "category": request.GET.get("category") or "", "q": (request.GET.get("q") or "").strip()}
    tickets = services.queue_queryset(request.user, scope=scope, category=filters["category"], query=filters["q"])
    page_obj = Paginator(tickets, PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [{"ticket": ticket, "requester": _person(ticket.user)} for ticket in page_obj]
    counts = services.queue_counts(request.user)
    return render_dashboard_page(
        request,
        full_template="support/queue.html",
        partial_template="support/partials/queue_content.html",
        context={
            "page_obj": page_obj,
            "rows": rows,
            "filters": filters,
            "scopes": [(code, label, counts.get(code)) for code, label in services.QUEUE_SCOPES],
            "counts": counts,
            "categories": SupportTicket.Category.choices,
            "pagination_query": urlencode({key: value for key, value in filters.items() if value}),
        },
        page_title=build_page_title("Очередь обращений", "Поддержка"),
        active_menu_key="support_queue",
    )


@login_required
def attachment_download(request, attachment_id: int):
    attachment = get_object_or_404(SupportAttachment.objects.select_related("message", "message__ticket"), pk=attachment_id)
    message = attachment.message
    staff = is_support_staff(request.user)
    if not services.can_view_ticket(request.user, message.ticket) or (message.is_internal and not staff):
        raise Http404
    try:
        handle = attachment.file.open("rb")
    except (FileNotFoundError, ValueError) as exc:
        raise Http404 from exc
    response = FileResponse(
        handle,
        content_type=attachment.content_type or "application/octet-stream",
        as_attachment=not attachment.is_image,
        filename=attachment.original_name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response
