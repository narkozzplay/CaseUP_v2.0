from django.urls import path

from .views import (
    admin_add_avatar_view,
    admin_add_background_view,
    admin_delete_avatar_view,
    admin_delete_background_view,
    catalog_view,
    clear_avatar_view,
    clear_background_view,
    select_avatar_view,
    select_background_view,
)

app_name = "profile_customization"

urlpatterns = [
    path("", catalog_view, name="catalog"),
    path("admin/add-background/", admin_add_background_view, name="admin_add_background"),
    path("admin/add-avatar/", admin_add_avatar_view, name="admin_add_avatar"),
    path("background/<int:background_id>/select/", select_background_view, name="select_background"),
    path("background/clear/", clear_background_view, name="clear_background"),
    path("avatar/<int:avatar_id>/select/", select_avatar_view, name="select_avatar"),
    path("avatar/clear/", clear_avatar_view, name="clear_avatar"),
    path("admin/background/<int:background_id>/delete/", admin_delete_background_view, name="admin_delete_background"),
    path("admin/avatar/<int:avatar_id>/delete/", admin_delete_avatar_view, name="admin_delete_avatar"),
]
