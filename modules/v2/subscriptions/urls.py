from django.urls import path

from .views import (
    balance_topup_placeholder_view,
    farmer_plans_page,
    plans_page,
    pricing_dashboard_page,
    purchase_plan_with_balance_view,
)

app_name = "subscriptions"

urlpatterns = [
    path("", plans_page, name="plans"),
    path("farmer/", farmer_plans_page, name="farmer_plans"),
    path("pricing/", pricing_dashboard_page, name="pricing_dashboard"),
    path("purchase/<int:plan_id>/", purchase_plan_with_balance_view, name="purchase_plan"),
    path("balance/topup/", balance_topup_placeholder_view, name="balance_topup"),
]