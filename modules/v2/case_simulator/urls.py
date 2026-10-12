from django.urls import path

from . import views

app_name = "case_simulator"

urlpatterns = [
    path("", views.case_list, name="list"),
    path("inventory/", views.inventory, name="inventory"),
    path("terminal/<slug:slug>/", views.terminal_page, name="terminal"),
    path("terminal/<slug:slug>/start/", views.terminal_start, name="terminal_start"),
    path("terminal/session/<int:session_id>/accept/", views.terminal_accept, name="terminal_accept"),
    path("terminal/session/<int:session_id>/decline/", views.terminal_decline, name="terminal_decline"),
    path("<slug:slug>/", views.case_detail, name="detail"),
    path("<slug:slug>/open/", views.case_open, name="open"),
]
