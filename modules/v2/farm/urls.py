from django.urls import path

from . import views

app_name = "farm"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("hub/", views.hub, name="hub"),
    path("accounts/", views.accounts_list, name="accounts_list"),
    path("accounts/add/", views.accounts_add, name="accounts_add"),
    path("accounts/refresh-profiles/", views.accounts_refresh_profiles, name="accounts_refresh_profiles"),
    path("accounts/<int:account_id>/", views.account_detail, name="accounts_detail"),
    path("accounts/<int:account_id>/edit/", views.accounts_edit, name="accounts_edit"),
    path("accounts/<int:account_id>/delete/", views.accounts_delete, name="accounts_delete"),
    path("accounts/<int:account_id>/sync-inventory/", views.account_inventory_sync, name="account_inventory_sync"),
    path("drops/", views.drops_list, name="drops_list"),
    path("drops/add/", views.drops_add, name="drops_add"),
    path("sessions/", views.sessions_list, name="sessions_list"),
    path("sessions/add/", views.sessions_add, name="sessions_add"),
    path("items/search/", views.item_search, name="item_search"),
    path("sessions/<int:session_id>/", views.sessions_detail, name="sessions_detail"),
    path("drop-pool/", views.drop_pool_manage, name="drop_pool"),
    path("drop-pool/<int:entry_id>/remove/", views.drop_pool_remove, name="drop_pool_remove"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/dispatch-thresholds/", views.settings_dispatch_thresholds, name="settings_dispatch_thresholds"),
    path("settings/dispatch-advantage/", views.settings_dispatch_advantage, name="settings_dispatch_advantage"),
]
