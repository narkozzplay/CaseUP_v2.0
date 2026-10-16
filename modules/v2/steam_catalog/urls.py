from django.urls import path

from . import views

app_name = "steam_catalog"

urlpatterns = [
    path("", views.catalog_list, name="list"),
    path("add/", views.catalog_add, name="add"),
    path("market-search/", views.catalog_market_search, name="market_search"),
    path("reference/", views.catalog_reference, name="reference"),
    path("reference/import-all/", views.catalog_reference_import_all, name="reference_import_all"),
    path("reference/<int:reference_id>/add/", views.catalog_reference_add, name="reference_add"),
    path("<int:item_id>/", views.catalog_detail, name="detail"),
    path("<int:item_id>/favorite/", views.toggle_favorite, name="favorite"),
    path("<int:item_id>/name-ru/", views.update_name_ru, name="update_name_ru"),
    path("<int:item_id>/price-update/", views.update_price, name="update_price"),
    path("<int:item_id>/stock/", views.add_stock_move, name="add_stock_move"),
    path("alerts/rules/", views.user_alert_rule_list, name="alert_rules"),
    path("alerts/rules/create/", views.user_alert_rule_create, name="alert_rule_create"),
    path("alerts/rules/<int:rule_id>/edit/", views.user_alert_rule_edit, name="alert_rule_edit"),
    path("alerts/rules/<int:rule_id>/toggle/", views.user_alert_rule_toggle, name="alert_rule_toggle"),
    path("alerts/history/", views.user_alert_event_history, name="alert_history"),
    path("alerts/history/<int:rule_id>/", views.user_alert_event_history, name="alert_rule_history"),
    path("alerts/help/", views.user_alert_help, name="alert_help"),
]