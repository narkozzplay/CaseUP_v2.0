from django.urls import path

from .views import dev_checkout_view, freekassa_return_view, payment_recheck_view, payment_return_view, topup_view

app_name = "payments"

urlpatterns = [
    path("topup/", topup_view, name="topup"),
    path("return/<uuid:public_id>/", payment_return_view, name="return"),
    path("recheck/<uuid:public_id>/", payment_recheck_view, name="recheck"),
    path("freekassa/return/", freekassa_return_view, name="freekassa_return"),
    path("dev-checkout/<uuid:public_id>/", dev_checkout_view, name="dev_checkout"),
]
