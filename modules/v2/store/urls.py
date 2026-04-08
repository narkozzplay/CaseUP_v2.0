from django.urls import path

from .views import storefront_page

app_name = "store"

urlpatterns = [
    path("", storefront_page, name="storefront"),
]
