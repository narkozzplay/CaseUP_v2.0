from django.urls import path

from . import views

app_name = "market"

urlpatterns = [
    path("", views.showcase_page, name="index"),
    path("lot/<int:listing_id>/", views.lot_page, name="lot"),
    path("sell/", views.sell_page, name="sell"),
    path("sell/trade-link/", views.trade_link_save, name="trade_link"),
    path("sell/list/", views.list_item, name="list_item"),
    path("sell/lot/<int:listing_id>/price/", views.lot_price, name="lot_price"),
    path("sell/lot/<int:listing_id>/cancel/", views.lot_cancel, name="lot_cancel"),
    path("lot/<int:listing_id>/buy/", views.lot_buy, name="buy"),
    path("deals/", views.my_deals, name="deals"),
    path("payout/", views.payout_page, name="payout"),
    path("payout/request/", views.payout_request, name="payout_request"),
    path("payout/transfer/", views.payout_transfer, name="payout_transfer"),
    path("payout/<str:code>/cancel/", views.payout_cancel, name="payout_cancel"),
    path("deal/<str:code>/", views.deal_page, name="deal"),
    path("deal/<str:code>/<str:action>/", views.deal_action, name="deal_action"),
]
