from django.urls import path

from .views import server_detail_view, server_list_view

app_name = "game_servers"

urlpatterns = [
    path("", server_list_view, name="list"),
    path("<slug:slug>/", server_detail_view, name="detail"),
]
