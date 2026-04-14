from django.urls import path

from .views import purchase_theme_page, storefront_page

app_name = "store"

urlpatterns = [
    path("", storefront_page, name="storefront"),
    path("themes/<int:listing_id>/purchase/", purchase_theme_page, name="purchase_theme"),
]
