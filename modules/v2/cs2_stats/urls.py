from django.urls import path

from .views import (
    cs2_match_detail_view,
    cs2_match_history_view,
    cs2_stats_own_view,
    cs2_stats_user_view,
    player_detail_view,
    player_matches_view,
    player_search_view,
)

app_name = "cs2_stats"

urlpatterns = [
    path("", cs2_stats_own_view, name="own"),
    path("user/<int:user_id>/", cs2_stats_user_view, name="user"),
    path("matches/", cs2_match_history_view, name="match_history"),
    path("matches/<int:match_id>/", cs2_match_detail_view, name="match_detail"),
    path("players/", player_search_view, name="players"),
    path("players/<str:steam_id>/", player_detail_view, name="player"),
    path("players/<str:steam_id>/matches/", player_matches_view, name="player_matches"),
]
