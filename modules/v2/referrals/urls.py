from django.urls import path

from .views import referrals_dashboard

app_name = "referrals"

urlpatterns = [
    path("", referrals_dashboard, name="dashboard"),
]
