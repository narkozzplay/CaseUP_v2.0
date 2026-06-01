from django.urls import path

from .views import (
    accept_friend_request_view,
    block_user_view,
    cancel_friend_request_view,
    decline_friend_request_view,
    friend_requests_view,
    friends_view,
    privacy_settings_view,
    remove_friend_view,
    send_friend_request_view,
    unblock_user_view,
)

app_name = "social_friends"

urlpatterns = [
    path("friends/", friends_view, name="friends"),
    path("friends/requests/", friend_requests_view, name="friend_requests"),
    path("friends/privacy/", privacy_settings_view, name="privacy_settings"),
    path("friends/request/<int:user_id>/send/", send_friend_request_view, name="send_friend_request"),
    path("friends/request/<int:request_id>/cancel/", cancel_friend_request_view, name="cancel_friend_request"),
    path("friends/request/<int:request_id>/accept/", accept_friend_request_view, name="accept_friend_request"),
    path("friends/request/<int:request_id>/decline/", decline_friend_request_view, name="decline_friend_request"),
    path("friends/<int:user_id>/remove/", remove_friend_view, name="remove_friend"),
    path("friends/<int:user_id>/block/", block_user_view, name="block_user"),
    path("friends/<int:user_id>/unblock/", unblock_user_view, name="unblock_user"),
]
