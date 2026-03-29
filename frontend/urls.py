from django.urls import path

from .views import admin_panel, dashboard_preview, home, testing_preview

app_name = "frontend"

urlpatterns = [
    path("", home, name="home"),
    path("dashboard-preview/", dashboard_preview, name="dashboard_preview"),
    path("testing-preview/", testing_preview, name="testing_preview"),
    path("control-center/", admin_panel, name="admin_panel"),
]