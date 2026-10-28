from django.urls import path

from .views import major_detail, major_index, major_leaderboard, pickem, pickem_trade_link

app_name = "majors"

urlpatterns = [
    path("", major_index, name="index"),
    path("<slug:slug>/", major_detail, name="detail"),
    path("<slug:slug>/pickem/", pickem, name="pickem"),
    path("<slug:slug>/pickem/trade-link/", pickem_trade_link, name="trade_link"),
    path("<slug:slug>/leaderboard/", major_leaderboard, name="leaderboard"),
]
