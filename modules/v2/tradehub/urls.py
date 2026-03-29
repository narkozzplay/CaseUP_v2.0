from django.urls import path

from . import views

app_name = "tradehub"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sales/", views.sales_list, name="sales_list"),
    path("sales/create/", views.create_order, name="create_order"),
    path("sales/<int:order_id>/", views.sale_detail, name="sale_detail"),
    path("sales/<int:order_id>/action/", views.sale_action, name="sale_action"),
    path("bots/", views.bots_list, name="bots_list"),
    path("bots/add/", views.bots_add, name="bots_add"),
]
