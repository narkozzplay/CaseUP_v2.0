from django.urls import path

from .views import admin_panel, agreement_page, dashboard_preview, home

app_name = "frontend"

urlpatterns = [
    path("", home, name="home"),
    path("dashboard-preview/", dashboard_preview, name="dashboard_preview"),
    path("agreement/", agreement_page, name="agreement"),
    path("control-center/", admin_panel, name="admin_panel"),
]