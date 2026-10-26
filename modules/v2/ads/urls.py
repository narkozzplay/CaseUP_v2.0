from django.urls import path

from .views import banner_click

app_name = "ads"

urlpatterns = [
    path("go/<int:banner_id>/", banner_click, name="go"),
]
