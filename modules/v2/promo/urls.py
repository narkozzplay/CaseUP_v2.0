from django.urls import path

from .views import promo_dashboard

app_name = "promo"

urlpatterns = [
    path("", promo_dashboard, name="dashboard"),
]
