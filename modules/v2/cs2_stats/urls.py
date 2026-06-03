from django.urls import path

from .views import cs2_stats_own_view, cs2_stats_user_view

app_name = "cs2_stats"

urlpatterns = [
    path("", cs2_stats_own_view, name="own"),
    path("user/<int:user_id>/", cs2_stats_user_view, name="user"),
]
