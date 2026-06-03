from django.urls import path

from .views import (
    profile_admin_update_view,
    profile_avatar_upload_view,
    profile_detail_view,
    profile_settings_view,
    profile_start_direct_chat_view,
    profile_theme_preferences_update_view,
    profile_user_settings_view,
    profile_view,
    sync_faceit_view,
    sync_steam_view,
    telegram_connect_view,
    telegram_disconnect_view,
    users_catalog_view,
    verification_requests_view,
)

app_name = "profiles"

urlpatterns = [
    path("profile/", profile_view, name="profile"),
    path("cabinet/", profile_view, name="cabinet"),
    path("profile/settings/", profile_settings_view, name="profile_settings"),
    path("users/", users_catalog_view, name="users_catalog"),
    path("verification-requests/", verification_requests_view, name="verification_requests"),
    path("profile/user/<int:user_id>/", profile_detail_view, name="profile_detail"),
    path("profile/user/<int:user_id>/settings/", profile_user_settings_view, name="profile_user_settings"),
    path("profile/user/<int:user_id>/start-chat/", profile_start_direct_chat_view, name="profile_start_direct_chat"),
    path("profile/user/<int:user_id>/admin-update/", profile_admin_update_view, name="profile_admin_update"),
    path("profile/theme/preferences/", profile_theme_preferences_update_view, name="profile_theme_preferences_update"),
    path("profile/avatar/upload/", profile_avatar_upload_view, name="profile_avatar_upload"),
    path("profile/sync-faceit/", sync_faceit_view, name="sync_faceit"),
    path("profile/sync-steam/", sync_steam_view, name="sync_steam"),
    path("profile/telegram/connect/", telegram_connect_view, name="telegram_connect"),
    path("profile/telegram/disconnect/", telegram_disconnect_view, name="telegram_disconnect"),
]
