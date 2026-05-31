from django.urls import path

from .views import (
    chats_view,
    conversation_create_view,
    conversations_view,
    group_create_view,
    groups_view,
    space_detail_view,
)

app_name = "social"

urlpatterns = [
    path("chats/", chats_view, name="chats"),
    path("groups/", groups_view, name="groups"),
    path("groups/create/", group_create_view, name="group_create"),
    path("conversations/", conversations_view, name="conversations"),
    path("conversations/create/", conversation_create_view, name="conversation_create"),
    path("spaces/<int:space_id>/<slug:slug>/", space_detail_view, name="space_detail"),
]
