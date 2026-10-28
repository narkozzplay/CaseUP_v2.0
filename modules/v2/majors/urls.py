from django.urls import path

from .views import major_detail, major_index, major_leaderboard, pickem

app_name = "majors"

urlpatterns = [
    path("", major_index, name="index"),
    path("<slug:slug>/", major_detail, name="detail"),
    path("<slug:slug>/pickem/", pickem, name="pickem"),
    path("<slug:slug>/leaderboard/", major_leaderboard, name="leaderboard"),
]
