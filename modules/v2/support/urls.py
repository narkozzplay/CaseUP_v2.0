from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.ticket_list, name="index"),
    path("new/", views.ticket_create, name="create"),
    path("queue/", views.staff_queue, name="queue"),
    path("<int:ticket_id>/", views.ticket_detail, name="detail"),
    path("<int:ticket_id>/reply/", views.ticket_reply, name="reply"),
    path("<int:ticket_id>/close/", views.ticket_close, name="close"),
    path("<int:ticket_id>/take/", views.ticket_take, name="take"),
    path("<int:ticket_id>/manage/", views.ticket_manage, name="manage"),
    path("attachment/<int:attachment_id>/", views.attachment_download, name="attachment"),
]
