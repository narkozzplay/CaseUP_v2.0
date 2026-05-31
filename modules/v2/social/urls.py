from django.urls import path

from .views import chats_view, conversations_view, groups_view

app_name = "social"

urlpatterns = [
    path("chats/", chats_view, name="chats"),
    path("groups/", groups_view, name="groups"),
    path("conversations/", conversations_view, name="conversations"),
]
