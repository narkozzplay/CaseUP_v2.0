from django.urls import path

from .views import bonus_claim, bonus_page

app_name = "daily_bonus"

urlpatterns = [
    path("", bonus_page, name="page"),
    path("claim/", bonus_claim, name="claim"),
]
