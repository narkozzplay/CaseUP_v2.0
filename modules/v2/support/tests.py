from __future__ import annotations

import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile, UserRole

from . import services
from .models import SupportAttachment, SupportMessage, SupportTicket
from .notifications import KIND_NEW, KIND_REPLY, KIND_STAFF, KIND_STATUS

User = get_user_model()
Status = SupportTicket.Status

MEDIA_DIR = tempfile.mkdtemp(prefix="support-tests-")


def make_user(username: str, role: str = UserRole.USER):
    user = User.objects.create_user(username=username, password="StrongPass123")
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save(update_fields=["role"])
    return User.objects.get(pk=user.pk)


def png(name: str = "shot.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "orange").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA_DIR)
class SupportTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)

    def setUp(self):
        self.user = make_user("sup_user")
        self.mod = make_user("sup_mod", UserRole.MOD)
        self.other = make_user("sup_other")

    def open_ticket(self, **kwargs) -> SupportTicket:
        params = {"user": self.user, "subject": "Не пришёл платёж", "category": "balance", "body": "Оплатил, баланс 0"}
        params.update(kwargs)
        return services.create_ticket(**params)


class TicketFlowTests(SupportTestCase):
    def test_create_ticket_with_attachment(self):
        ticket = self.open_ticket(files=[png()])
        self.assertEqual(ticket.status, Status.OPEN)
        message = ticket.messages.get()
        attachment = message.attachments.get()
        self.assertEqual(attachment.content_type, "image/png")
        self.assertEqual(attachment.original_name, "shot.png")
        self.assertTrue(attachment.file.name.startswith("private/support/"))
        self.assertNotIn("shot", attachment.file.name)

    def test_rejects_bad_files(self):
        with self.assertRaisesMessage(services.SupportError, "картинки"):
            self.open_ticket(files=[SimpleUploadedFile("x.html", b"<script>", content_type="text/html")])
        with self.assertRaisesMessage(services.SupportError, "не похож на картинку"):
            self.open_ticket(files=[SimpleUploadedFile("x.png", b"not an image", content_type="image/png")])
        with self.assertRaisesMessage(services.SupportError, "не больше"):
            self.open_ticket(files=[png(f"{i}.png") for i in range(services.MAX_ATTACHMENTS + 1)])
        self.assertFalse(SupportTicket.objects.exists())

    def test_active_ticket_limit(self):
        for index in range(services.MAX_ACTIVE_TICKETS):
            self.open_ticket(subject=f"Вопрос {index}")
        with self.assertRaises(services.SupportError):
            self.open_ticket()

    def test_staff_reply_notifies_user_and_hands_turn(self):
        ticket = self.open_ticket()
        with mock.patch("modules.v2.support.services.send_telegram_now") as telegram, self.captureOnCommitCallbacks(execute=True):
            services.add_reply(ticket=ticket, author=self.mod, body="Проверили, деньги зачислены")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.WAITING_USER)
        self.assertEqual(ticket.assignee, self.mod)
        self.assertTrue(ticket.user_unread)
        notification = Notification.objects.get(recipient=self.user, kind=KIND_REPLY)
        self.assertEqual(notification.payload["url"], reverse("support:detail", args=[ticket.pk]))
        telegram.assert_called_once()
        self.assertEqual(telegram.call_args.args[0], self.user.pk)

        services.add_reply(ticket=ticket, author=self.user, body="Спасибо, но не всё")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.IN_PROGRESS)
        self.assertTrue(Notification.objects.filter(recipient=self.mod, kind=KIND_STAFF, object_id=ticket.pk).exists())

    def test_internal_note_is_hidden_from_user(self):
        ticket = self.open_ticket()
        services.add_reply(ticket=ticket, author=self.mod, body="Похоже на мультиаккаунт", internal=True)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.OPEN)
        self.assertFalse(Notification.objects.filter(recipient=self.user).exists())
        self.assertEqual(services.visible_messages(ticket, staff=False).count(), 1)
        self.assertEqual(services.visible_messages(ticket, staff=True).count(), 2)

    def test_user_cannot_write_internal_or_into_closed(self):
        ticket = self.open_ticket()
        message = services.add_reply(ticket=ticket, author=self.user, body="ещё", internal=True)
        self.assertFalse(message.is_internal)
        services.close_by_user(ticket=ticket, user=self.user)
        with self.assertRaises(services.SupportError):
            services.add_reply(ticket=ticket, author=self.user, body="снова")
        with self.assertRaises(services.SupportError):
            services.add_reply(ticket=ticket, author=self.other, body="чужой")

    def test_take_and_status(self):
        ticket = self.open_ticket()
        services.take_ticket(ticket=ticket, moderator=self.mod)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.assignee), (Status.IN_PROGRESS, self.mod))
        with self.captureOnCommitCallbacks(execute=True):
            services.set_status(ticket=ticket, actor=self.mod, status=Status.RESOLVED)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closed_at)
        self.assertTrue(Notification.objects.filter(recipient=self.user, kind=KIND_STATUS).exists())
        with self.assertRaises(services.SupportError):
            services.set_status(ticket=ticket, actor=self.user, status=Status.CLOSED)
        # A reply to a resolved ticket reopens it.
        services.add_reply(ticket=ticket, author=self.user, body="Снова не работает")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.IN_PROGRESS)
        self.assertIsNone(ticket.closed_at)

    def test_counters(self):
        ticket = self.open_ticket()
        self.assertEqual(services.staff_queue_count(self.mod), 1)
        self.assertEqual(services.staff_queue_count(self.user), 0)
        services.add_reply(ticket=ticket, author=self.mod, body="Ответ")
        self.assertEqual(services.staff_queue_count(self.mod), 0)
        self.assertEqual(services.user_unread_count(self.user), 1)
        services.mark_seen_by_user(ticket)
        self.assertEqual(services.user_unread_count(self.user), 0)
        self.assertFalse(Notification.objects.filter(recipient=self.user, is_read=False).exists())

    def test_queue_scopes(self):
        first = self.open_ticket(subject="Первый")
        second = self.open_ticket(subject="Второй")
        services.take_ticket(ticket=second, moderator=self.mod)
        self.assertEqual(list(services.queue_queryset(self.mod, scope="new")), [first])
        self.assertEqual(list(services.queue_queryset(self.mod, scope="mine")), [second])
        self.assertEqual(list(services.queue_queryset(self.mod, scope="all", query=f"#{first.pk}")), [first])


class TelegramCopyTests(SupportTestCase):
    def test_sends_only_to_linked_users(self):
        with mock.patch("modules.v2.system.services.is_module_enabled", return_value=True), \
                mock.patch("modules.v2.tg_notifier.services.send_user_message") as send, \
                mock.patch("modules.v2.tg_notifier.services.ensure_public_base_url", return_value="https://case-up.ru"):
            self.assertFalse(services.send_telegram_now(self.user.pk, "text", "/support/1/"))
            send.assert_not_called()
            UserProfile.objects.filter(user=self.user).update(telegram_chat_id="12345")
            self.assertTrue(services.send_telegram_now(self.user.pk, "text", "/support/1/"))
            self.assertIn("https://case-up.ru/support/1/", send.call_args.kwargs["text"])
            self.assertFalse(send.call_args.kwargs["allow_default"])


class SupportViewTests(SupportTestCase):
    def test_user_creates_ticket_through_form(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("support:create")).status_code, 200)
        response = self.client.post(
            reverse("support:create"),
            {"subject": "Кейс не открылся", "category": "cases", "body": "Списало деньги", "attachments": [png()]},
        )
        ticket = SupportTicket.objects.get()
        self.assertRedirects(response, reverse("support:detail", args=[ticket.pk]), fetch_redirect_response=False)
        self.assertEqual(SupportAttachment.objects.count(), 1)
        page = self.client.get(reverse("support:index"))
        self.assertContains(page, "Кейс не открылся")

    def test_access_rules(self):
        ticket = self.open_ticket(files=[png()])
        attachment = SupportAttachment.objects.get()
        note = services.add_reply(ticket=ticket, author=self.mod, body="секрет", files=[png("n.png")], internal=True)
        note_file = note.attachments.get()

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("support:detail", args=[ticket.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("support:attachment", args=[attachment.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("support:queue")).status_code, 404)

        self.client.force_login(self.user)
        page = self.client.get(reverse("support:detail", args=[ticket.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "секрет")
        response = self.client.get(reverse("support:attachment", args=[attachment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get(reverse("support:attachment", args=[note_file.pk])).status_code, 404)

        self.client.force_login(self.mod)
        page = self.client.get(reverse("support:detail", args=[ticket.pk]))
        self.assertContains(page, "секрет")
        self.assertEqual(self.client.get(reverse("support:attachment", args=[note_file.pk])).status_code, 200)

    def test_moderator_works_ticket_from_queue(self):
        ticket = self.open_ticket()
        self.client.force_login(self.mod)
        queue = self.client.get(reverse("support:queue"))
        self.assertContains(queue, ticket.subject)
        self.client.post(reverse("support:take", args=[ticket.pk]))
        self.client.post(reverse("support:reply", args=[ticket.pk]), {"body": "Уже чиним"})
        self.client.post(reverse("support:manage", args=[ticket.pk]), {"status": Status.RESOLVED, "priority": "high"})
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.priority, ticket.assignee), (Status.RESOLVED, "high", self.mod))
        self.assertEqual(SupportMessage.objects.filter(ticket=ticket, is_staff=True, is_system=False).count(), 1)

        self.client.force_login(self.user)
        self.assertEqual(services.user_unread_count(self.user), 1)
        self.client.get(reverse("support:detail", args=[ticket.pk]))
        self.assertEqual(services.user_unread_count(self.user), 0)
        self.client.post(reverse("support:close", args=[ticket.pk]))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Status.CLOSED)

    def test_topbar_button_and_menu(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("support:index"))
        self.assertContains(page, "topbar-support")
        self.assertContains(page, 'data-menu-key="support"')
        self.assertNotContains(page, 'data-menu-key="support_queue"')
        self.client.force_login(self.mod)
        page = self.client.get(reverse("support:index"))
        self.assertContains(page, 'data-menu-key="support_queue"')


class StaffAlertTests(SupportTestCase):
    def test_new_ticket_notifies_every_moderator_on_the_site(self):
        admin = make_user("sup_admin", UserRole.ADMIN)
        with self.captureOnCommitCallbacks(execute=True):
            ticket = self.open_ticket()
        recipients = set(Notification.objects.filter(kind=KIND_NEW, object_id=ticket.pk).values_list("recipient__username", flat=True))
        self.assertEqual(recipients, {"sup_mod", "sup_admin"})
        self.assertEqual(Notification.objects.get(recipient=admin, kind=KIND_NEW).payload["url"], reverse("support:detail", args=[ticket.pk]))

        services.take_ticket(ticket=ticket, moderator=self.mod)
        self.assertFalse(Notification.objects.filter(kind=KIND_NEW, is_read=False).exists())

    def test_moderator_ticket_does_not_notify_its_author(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.create_ticket(user=self.mod, subject="Вопрос", category="other", body="Текст")
        self.assertFalse(Notification.objects.filter(kind=KIND_NEW).exists())

    def test_stale_tickets_are_escalated_once(self):
        stale = self.open_ticket(subject="Старый")
        fresh = self.open_ticket(subject="Свежий")
        taken = self.open_ticket(subject="Взятый")
        services.take_ticket(ticket=taken, moderator=self.mod)
        hours_ago = timezone.now() - timedelta(hours=4)
        SupportTicket.objects.filter(pk__in=[stale.pk, taken.pk]).update(created_at=hours_ago)
        UserProfile.objects.filter(user=self.mod).update(telegram_chat_id="777")

        with mock.patch("modules.v2.system.services.is_module_enabled", return_value=True), \
                mock.patch("modules.v2.tg_notifier.services.resolve_default_chat_id", return_value="-100team"), \
                mock.patch("modules.v2.tg_notifier.services.ensure_public_base_url", return_value="https://case-up.ru"), \
                mock.patch("modules.v2.tg_notifier.services.send_message") as send:
            self.assertEqual(services.escalate_stale_tickets(), 1)
            self.assertEqual(sorted(call.kwargs["chat_id"] for call in send.call_args_list), ["-100team", "777"])
            text = send.call_args.kwargs["text"]
            self.assertIn(f"#{stale.pk} «Старый»", text)
            self.assertNotIn("Свежий", text)
            self.assertNotIn("Взятый", text)
            self.assertIn(f"https://case-up.ru/support/{stale.pk}/", text)
            self.assertEqual(services.escalate_stale_tickets(), 0)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIsNotNone(stale.escalated_at)
        self.assertIsNone(fresh.escalated_at)

    def test_escalation_retries_when_telegram_fails(self):
        ticket = self.open_ticket()
        SupportTicket.objects.filter(pk=ticket.pk).update(created_at=timezone.now() - timedelta(hours=5))
        with mock.patch("modules.v2.system.services.is_module_enabled", return_value=True), \
                mock.patch("modules.v2.tg_notifier.services.resolve_default_chat_id", return_value="-100team"), \
                mock.patch("modules.v2.tg_notifier.services.send_message", side_effect=RuntimeError("down")):
            self.assertEqual(services.escalate_stale_tickets(), 0)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.escalated_at)

    def test_escalation_message_stays_short(self):
        for index in range(services.ESCALATION_BATCH + 2):
            self.open_ticket(user=make_user(f"sup_many_{index}"), subject="Очень длинная тема " * 8)
        SupportTicket.objects.update(created_at=timezone.now() - timedelta(hours=4))
        with mock.patch("modules.v2.system.services.is_module_enabled", return_value=True), \
                mock.patch("modules.v2.tg_notifier.services.resolve_default_chat_id", return_value="-100team"), \
                mock.patch("modules.v2.tg_notifier.services.ensure_public_base_url", return_value="https://case-up.ru"), \
                mock.patch("modules.v2.tg_notifier.services.send_message") as send:
            self.assertEqual(services.escalate_stale_tickets(), services.ESCALATION_BATCH)
            self.assertLess(len(send.call_args.kwargs["text"]), 4096)
            self.assertEqual(services.escalate_stale_tickets(), 2)
