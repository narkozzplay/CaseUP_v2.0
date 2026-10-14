from django.urls import path

from .views import (
    notification_mark_read_view,
    notification_open_view,
    notifications_list_view,
    notifications_mark_all_read_view,
    notifications_unread_count_view,
)

app_name = "notifications"

urlpatterns = [
    path("", notifications_list_view, name="list"),
    path("unread-count/", notifications_unread_count_view, name="unread_count"),
    path("read-all/", notifications_mark_all_read_view, name="mark_all_read"),
    path("<int:notification_id>/open/", notification_open_view, name="open"),
    path("<int:notification_id>/read/", notification_mark_read_view, name="mark_read"),
]
