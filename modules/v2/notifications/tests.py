from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Notification
from .registry import NotificationView, register_kind
from .services import (
    build_notification_rows,
    get_notifications,
    mark_all_read,
    mark_read,
    notify,
    render_notification,
    unread_count,
)


User = get_user_model()

register_kind(
    "test_event",
    lambda notification, lang: NotificationView(
        title="Тест" if lang == "ru" else "Test",
        url="/somewhere/",
    ),
)


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="n_alice", password="pass")
        self.bob = User.objects.create_user(username="n_bob", password="pass")

    def test_notify_and_count(self):
        notify(self.bob, "test_event", actor=self.alice, object_id=1)
        notify(self.bob, "test_event", actor=self.alice, object_id=2)
        self.assertEqual(unread_count(self.bob), 2)
        self.assertEqual(unread_count(self.alice), 0)

    def test_notify_skips_self(self):
        self.assertIsNone(notify(self.alice, "test_event", actor=self.alice))
        self.assertEqual(Notification.objects.count(), 0)

    def test_dedupe_reopens_existing(self):
        first = notify(self.bob, "test_event", actor=self.alice, object_id=7)
        mark_read(self.bob, first.pk)
        second = notify(self.bob, "test_event", actor=self.alice, object_id=7)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(unread_count(self.bob), 1)

    def test_mark_read_only_own(self):
        notification = notify(self.bob, "test_event", actor=self.alice)
        self.assertFalse(mark_read(self.alice, notification.pk))
        self.assertTrue(mark_read(self.bob, notification.pk))
        self.assertEqual(unread_count(self.bob), 0)

    def test_mark_all_read(self):
        notify(self.bob, "test_event", object_id=1)
        notify(self.bob, "test_event", object_id=2)
        self.assertEqual(mark_all_read(self.bob), 2)
        self.assertEqual(unread_count(self.bob), 0)

    def test_unknown_kind_falls_back(self):
        notification = notify(self.bob, "no_such_kind")
        view = render_notification(notification, "ru")
        self.assertEqual(view.title, "Новое уведомление")

    def test_rows_use_language(self):
        notify(self.bob, "test_event")
        rows = build_notification_rows(get_notifications(self.bob), "en")
        self.assertEqual(rows[0]["view"].title, "Test")


class NotificationViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="v_alice", password="pass")
        self.bob = User.objects.create_user(username="v_bob", password="pass")
        self.client = Client()
        self.client.force_login(self.bob)

    def test_list_page(self):
        notify(self.bob, "test_event", actor=self.alice)
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тест")
        self.assertContains(response, "notif-item is-unread")

    def test_list_unread_filter(self):
        read = notify(self.bob, "test_event", object_id=1)
        mark_read(self.bob, read.pk)
        notify(self.bob, "test_event", object_id=2)
        response = self.client.get(reverse("notifications:list") + "?filter=unread")
        self.assertEqual(len(response.context["notification_rows"]), 1)

    def test_open_marks_read_and_redirects(self):
        notification = notify(self.bob, "test_event", actor=self.alice)
        response = self.client.get(reverse("notifications:open", args=[notification.pk]))
        self.assertRedirects(response, "/somewhere/", fetch_redirect_response=False)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_open_foreign_notification_404(self):
        notification = notify(self.alice, "test_event", actor=self.bob)
        response = self.client.get(reverse("notifications:open", args=[notification.pk]))
        self.assertEqual(response.status_code, 404)

    def test_mark_all_read_requires_post(self):
        self.assertEqual(self.client.get(reverse("notifications:mark_all_read")).status_code, 405)
        notify(self.bob, "test_event")
        self.client.post(reverse("notifications:mark_all_read"))
        self.assertEqual(unread_count(self.bob), 0)

    def test_unread_count_json(self):
        notify(self.bob, "test_event")
        response = self.client.get(reverse("notifications:unread_count"))
        self.assertEqual(response.json(), {"unread": 1})

    def test_login_required(self):
        response = Client().get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 302)
