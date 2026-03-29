from django.urls import path

from . import views

app_name = "steam_trading"

urlpatterns = [
    path("", views.purchases_list, name="list"),
    path("investments/", views.investments_dashboard, name="investments"),
    path("add/", views.purchase_add, name="add"),
    path("<int:purchase_id>/sell/", views.purchase_sell, name="sell"),
    path("<int:purchase_id>/send-tg/", views.purchase_send_telegram, name="send_tg"),
]