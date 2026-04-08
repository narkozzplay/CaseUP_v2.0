from django.urls import path

from .views import dashboard_page

app_name = "shop"

urlpatterns = [
    path("", dashboard_page, name="dashboard"),
]
