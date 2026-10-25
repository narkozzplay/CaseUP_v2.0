from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.rbac import get_user_role

from .models import SupportAttachment, SupportMessage, SupportTicket

logger = logging.getLogger(__name__)

MAX_ACTIVE_TICKETS = 5
MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".log"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS
ACCEPT_ATTRIBUTE = ",".join(sorted(ALLOWED_EXTENSIONS))

Status = SupportTicket.Status


class SupportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def is_support_staff(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and get_user_role(user).is_moder_plus)


def can_view_ticket(user, ticket: SupportTicket) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return ticket.user_id == user.pk or is_support_staff(user)


def visible_messages(ticket: SupportTicket, *, staff: bool):
    qs = ticket.messages.select_related("author", "author__profile_v2").prefetch_related("attachments")
    if not staff:
        qs = qs.filter(is_internal=False)
    return qs


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _validate_files(files) -> list[tuple[object, str]]:
    files = [item for item in (files or []) if item]
    if len(files) > MAX_ATTACHMENTS:
        raise SupportError(f"Можно приложить не больше {MAX_ATTACHMENTS} файлов к одному сообщению.")
    validated = []
    for upload in files:
        name = Path(str(upload.name or "file")).name[:160]
        extension = Path(name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise SupportError(f"Файл «{name}»: можно прикладывать картинки (PNG, JPG, WEBP, GIF), PDF и TXT.")
        if upload.size > MAX_ATTACHMENT_BYTES:
            raise SupportError(f"Файл «{name}» больше {MAX_ATTACHMENT_BYTES // (1024 * 1024)} МБ.")
        if extension in IMAGE_EXTENSIONS:
            _verify_image(upload, name)
        # The type comes from the extension we allowed, never from what the browser claims.
        content_type = mimetypes.types_map.get(extension, "") or ("text/plain" if extension == ".log" else "application/octet-stream")
        validated.append((upload, content_type))
    return validated


def _verify_image(upload, name: str) -> None:
    from PIL import Image

    try:
        upload.seek(0)
        with Image.open(upload) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 - any decoder failure means it is not a picture
        raise SupportError(f"Файл «{name}» не похож на картинку.") from exc
    finally:
        upload.seek(0)


def _save_attachments(message: SupportMessage, validated) -> None:
    for upload, content_type in validated:
        SupportAttachment.objects.create(
            message=message,
            file=upload,
            original_name=Path(str(upload.name or "file")).name[:160],
            content_type=content_type,
            size=upload.size,
        )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _clean_body(body: str) -> str:
    return (body or "").strip()[:5000]


def _system_line(ticket: SupportTicket, actor, text: str) -> SupportMessage:
    return SupportMessage.objects.create(ticket=ticket, author=actor, body=text, is_staff=True, is_system=True)


def create_ticket(*, user, subject: str, category: str, body: str, files=None, page_url: str = "") -> SupportTicket:
    subject = (subject or "").strip()[:160]
    body = _clean_body(body)
    if not subject:
        raise SupportError("Укажите тему обращения.")
    if not body:
        raise SupportError("Опишите проблему.")
    if category not in SupportTicket.Category.values:
        category = SupportTicket.Category.OTHER
    if SupportTicket.objects.filter(user=user, status__in=SupportTicket.ACTIVE_STATUSES).count() >= MAX_ACTIVE_TICKETS:
        raise SupportError(f"У вас уже {MAX_ACTIVE_TICKETS} открытых обращений. Дождитесь ответа по ним или закройте решённые.")
    validated = _validate_files(files)

    with transaction.atomic():
        ticket = SupportTicket.objects.create(
            user=user,
            subject=subject,
            category=category,
            page_url=(page_url or "")[:300],
            last_message_at=timezone.now(),
        )
        message = SupportMessage.objects.create(ticket=ticket, author=user, body=body)
        _save_attachments(message, validated)
    return ticket


def add_reply(*, ticket: SupportTicket, author, body: str, files=None, internal: bool = False) -> SupportMessage:
    """A message in the thread. A staff reply hands the turn to the user; a user reply hands it back."""
    body = _clean_body(body)
    staff_side = ticket.user_id != author.pk and is_support_staff(author)
    if not staff_side and ticket.user_id != author.pk:
        raise SupportError("Нет доступа к обращению.")
    if internal and not staff_side:
        internal = False
    if not body and not files:
        raise SupportError("Сообщение пустое.")
    if ticket.is_closed and not staff_side:
        raise SupportError("Обращение закрыто. Если вопрос остался, создайте новое.")
    validated = _validate_files(files)

    with transaction.atomic():
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        message = SupportMessage.objects.create(
            ticket=ticket,
            author=author,
            body=body,
            is_staff=staff_side,
            is_internal=internal,
        )
        _save_attachments(message, validated)
        if internal:
            return message

        now = timezone.now()
        ticket.last_message_at = now
        fields = ["last_message_at", "updated_at"]
        if staff_side:
            if ticket.assignee_id is None:
                ticket.assignee = author
                fields.append("assignee")
            ticket.status = Status.WAITING_USER
            ticket.closed_at = None
            ticket.user_unread = True
            fields += ["status", "closed_at", "user_unread"]
        else:
            ticket.status = Status.IN_PROGRESS if ticket.assignee_id else Status.OPEN
            ticket.closed_at = None
            fields += ["status", "closed_at"]
        ticket.save(update_fields=fields)

    if staff_side:
        _notify_user_reply(ticket, message)
    else:
        _notify_assignee(ticket, message)
    return message


def take_ticket(*, ticket: SupportTicket, moderator) -> SupportTicket:
    if not is_support_staff(moderator):
        raise SupportError("Брать обращения в работу может только модератор.")
    with transaction.atomic():
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        if ticket.assignee_id == moderator.pk and ticket.status == Status.IN_PROGRESS:
            return ticket
        ticket.assignee = moderator
        if ticket.status in (Status.OPEN, Status.RESOLVED, Status.CLOSED):
            ticket.status = Status.IN_PROGRESS
            ticket.closed_at = None
        ticket.user_unread = True
        ticket.save(update_fields=["assignee", "status", "closed_at", "user_unread", "updated_at"])
        _system_line(ticket, moderator, "Обращение взято в работу.")
    return ticket


def set_status(*, ticket: SupportTicket, actor, status: str) -> SupportTicket:
    if status not in Status.values:
        raise SupportError("Неизвестный статус.")
    if not is_support_staff(actor):
        raise SupportError("Менять статус может только модератор.")
    with transaction.atomic():
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        if ticket.status == status:
            return ticket
        ticket.status = status
        ticket.closed_at = timezone.now() if status in (Status.RESOLVED, Status.CLOSED) else None
        if ticket.assignee_id is None:
            ticket.assignee = actor
        ticket.user_unread = True
        ticket.save(update_fields=["status", "closed_at", "assignee", "user_unread", "updated_at"])
        _system_line(ticket, actor, f"Статус: {Status(status).label}.")
    if status in (Status.RESOLVED, Status.CLOSED):
        _notify_user_status(ticket)
    return ticket


def set_priority(*, ticket: SupportTicket, actor, priority: str) -> SupportTicket:
    if priority not in SupportTicket.Priority.values or not is_support_staff(actor):
        raise SupportError("Нельзя изменить приоритет.")
    if ticket.priority != priority:
        ticket.priority = priority
        ticket.save(update_fields=["priority", "updated_at"])
    return ticket


def close_by_user(*, ticket: SupportTicket, user) -> SupportTicket:
    if ticket.user_id != user.pk:
        raise SupportError("Нет доступа к обращению.")
    if ticket.is_closed:
        return ticket
    with transaction.atomic():
        ticket.status = Status.CLOSED
        ticket.closed_at = timezone.now()
        ticket.save(update_fields=["status", "closed_at", "updated_at"])
        SupportMessage.objects.create(ticket=ticket, author=user, body="Пользователь закрыл обращение.", is_system=True)
    return ticket


def mark_seen_by_user(ticket: SupportTicket) -> None:
    ticket.user_unread = False
    if SupportTicket.objects.filter(pk=ticket.pk, user_unread=True).update(user_unread=False):
        from modules.v2.notifications.services import resolve_notifications

        from .notifications import USER_KINDS

        for kind in USER_KINDS:
            resolve_notifications(kind, ticket.pk, recipient=ticket.user)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def user_unread_count(user) -> int:
    if not getattr(user, "is_authenticated", False):
        return 0
    return SupportTicket.objects.filter(user=user, user_unread=True).count()


def staff_queue_count(user) -> int:
    """New tickets nobody took yet, plus the moderator's own tickets where the user wrote last."""
    if not is_support_staff(user):
        return 0
    return SupportTicket.objects.filter(
        Q(status=Status.OPEN, assignee__isnull=True) | Q(assignee=user, status__in=SupportTicket.STAFF_TURN_STATUSES)
    ).count()


QUEUE_SCOPES = (
    ("new", "Новые"),
    ("mine", "Мои"),
    ("active", "Все открытые"),
    ("waiting", "Ждут пользователя"),
    ("done", "Решённые и закрытые"),
    ("all", "Все"),
)


def queue_queryset(user, *, scope: str = "new", category: str = "", query: str = ""):
    qs = SupportTicket.objects.select_related("user", "user__profile_v2", "assignee", "assignee__profile_v2")
    if scope == "new":
        qs = qs.filter(status=Status.OPEN, assignee__isnull=True)
    elif scope == "mine":
        qs = qs.filter(assignee=user, status__in=SupportTicket.ACTIVE_STATUSES)
    elif scope == "active":
        qs = qs.filter(status__in=SupportTicket.ACTIVE_STATUSES)
    elif scope == "waiting":
        qs = qs.filter(status=Status.WAITING_USER)
    elif scope == "done":
        qs = qs.filter(status__in=(Status.RESOLVED, Status.CLOSED))
    if category in SupportTicket.Category.values:
        qs = qs.filter(category=category)
    query = (query or "").strip()
    if query:
        condition = Q(subject__icontains=query) | Q(user__username__icontains=query) | Q(user__profile_v2__steam_personaname__icontains=query)
        if query.lstrip("#").isdigit():
            condition |= Q(pk=int(query.lstrip("#")))
        qs = qs.filter(condition)
    if scope in ("new", "mine", "active"):
        # Oldest waiting first, high priority on top: the queue is worked from the top.
        return qs.order_by(_priority_order(), "last_message_at", "id")
    return qs.order_by("-last_message_at", "-id")


def _priority_order():
    from django.db.models import Case, IntegerField, Value, When

    return Case(
        When(priority=SupportTicket.Priority.HIGH, then=Value(0)),
        When(priority=SupportTicket.Priority.NORMAL, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )


def queue_counts(user) -> dict[str, int]:
    base = SupportTicket.objects
    return {
        "new": base.filter(status=Status.OPEN, assignee__isnull=True).count(),
        "mine": base.filter(assignee=user, status__in=SupportTicket.ACTIVE_STATUSES).count(),
        "active": base.filter(status__in=SupportTicket.ACTIVE_STATUSES).count(),
        "waiting": base.filter(status=Status.WAITING_USER).count(),
    }


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _snippet(text: str, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _notify_user_reply(ticket: SupportTicket, message: SupportMessage) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_REPLY

    url = reverse("support:detail", args=[ticket.pk])
    body = _snippet(message.body) or "Во вложении файл."
    try:
        notify(
            ticket.user,
            KIND_REPLY,
            object_id=ticket.pk,
            payload={"title": f"Ответ поддержки · #{ticket.pk} {ticket.subject}"[:200], "body": body, "url": url},
        )
    except Exception:  # noqa: BLE001 - the reply is saved either way
        logger.exception("Failed to notify about a support reply in ticket %s.", ticket.pk)
    _send_telegram(ticket.user_id, f"💬 Ответ поддержки по обращению #{ticket.pk} «{ticket.subject}»\n\n{body}", url)


def _notify_user_status(ticket: SupportTicket) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_STATUS

    url = reverse("support:detail", args=[ticket.pk])
    label = Status(ticket.status).label.lower()
    body = "Если вопрос не решён, ответьте в обращении." if ticket.status == Status.RESOLVED else "Если вопрос остался, создайте новое обращение."
    try:
        notify(
            ticket.user,
            KIND_STATUS,
            object_id=ticket.pk,
            payload={"title": f"Обращение #{ticket.pk} {label}"[:200], "body": body, "url": url},
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to notify about a support status in ticket %s.", ticket.pk)
    _send_telegram(ticket.user_id, f"✅ Обращение #{ticket.pk} «{ticket.subject}» {label}.\n\n{body}", url)


def _notify_assignee(ticket: SupportTicket, message: SupportMessage) -> None:
    if not ticket.assignee_id:
        return
    from modules.v2.notifications.services import notify

    from .notifications import KIND_STAFF

    try:
        notify(
            ticket.assignee,
            KIND_STAFF,
            actor=ticket.user,
            object_id=ticket.pk,
            payload={
                "title": f"Новое сообщение в обращении #{ticket.pk}"[:200],
                "body": _snippet(message.body) or "Во вложении файл.",
                "url": reverse("support:detail", args=[ticket.pk]),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to notify the assignee of ticket %s.", ticket.pk)


def _send_telegram(user_id: int, text: str, url: str) -> None:
    from .tasks import send_telegram_copy

    transaction.on_commit(lambda: send_telegram_copy.delay(user_id, text, url))


def send_telegram_now(user_id: int, text: str, url: str) -> bool:
    """The Telegram copy of a site notification; only for users who linked Telegram, never the default chat."""
    from django.contrib.auth import get_user_model

    from modules.v2.system.services import is_module_enabled
    from modules.v2.tg_notifier.services import ensure_public_base_url, send_user_message

    if not is_module_enabled("tg_notifier"):
        return False
    user = get_user_model().objects.filter(pk=user_id).select_related("profile_v2").first()
    profile = getattr(user, "profile_v2", None) if user else None
    if profile is None or not profile.telegram_is_linked:
        return False
    try:
        link = f"{ensure_public_base_url()}{url}"
    except Exception:  # noqa: BLE001
        link = ""
    try:
        send_user_message(user=user, text=f"{text}\n\n{link}".strip(), allow_default=False)
    except Exception:  # noqa: BLE001 - Telegram is a best-effort copy
        logger.warning("Support Telegram copy to user %s failed.", user_id, exc_info=True)
        return False
    return True
