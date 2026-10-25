from django.urls import path

from .views import donate_page, donate_submit

app_name = "donations"

urlpatterns = [
    path("", donate_page, name="page"),
    path("give/", donate_submit, name="give"),
]
