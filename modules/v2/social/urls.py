from django.urls import path

from .views import (
    chats_view,
    conversation_create_view,
    conversations_view,
    group_create_view,
    groups_view,
    space_detail_view,
    space_leave_view,
    space_member_add_view,
    space_member_block_view,
    space_member_remove_view,
    space_member_role_view,
    space_member_transfer_owner_view,
    space_member_unblock_view,
    space_members_manage_view,
    space_message_create_view,
)

app_name = "social"

urlpatterns = [
    path("chats/", chats_view, name="chats"),
    path("groups/", groups_view, name="groups"),
    path("groups/create/", group_create_view, name="group_create"),
    path("conversations/", conversations_view, name="conversations"),
    path("conversations/create/", conversation_create_view, name="conversation_create"),
    path("spaces/<int:space_id>/<slug:slug>/", space_detail_view, name="space_detail"),
    path("spaces/<int:space_id>/<slug:slug>/messages/send/", space_message_create_view, name="space_message_create"),
    path("spaces/<int:space_id>/<slug:slug>/members/", space_members_manage_view, name="space_members_manage"),
    path("spaces/<int:space_id>/<slug:slug>/members/add/", space_member_add_view, name="space_member_add"),
    path("spaces/<int:space_id>/<slug:slug>/members/<int:user_id>/remove/", space_member_remove_view, name="space_member_remove"),
    path("spaces/<int:space_id>/<slug:slug>/members/<int:user_id>/role/", space_member_role_view, name="space_member_role"),
    path("spaces/<int:space_id>/<slug:slug>/members/<int:user_id>/block/", space_member_block_view, name="space_member_block"),
    path("spaces/<int:space_id>/<slug:slug>/members/<int:user_id>/unblock/", space_member_unblock_view, name="space_member_unblock"),
    path("spaces/<int:space_id>/<slug:slug>/members/<int:user_id>/transfer-owner/", space_member_transfer_owner_view, name="space_member_transfer_owner"),
    path("spaces/<int:space_id>/<slug:slug>/leave/", space_leave_view, name="space_leave"),
]
