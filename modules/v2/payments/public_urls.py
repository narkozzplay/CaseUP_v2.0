from django.urls import path

from .views import webhook_view

app_name = "payments_public"

urlpatterns = [
    path("webhook/<slug:provider_code>/", webhook_view, name="webhook"),
]
