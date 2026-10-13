from django.urls import path

from . import views

app_name = "control_center"

urlpatterns = [
    path("site/toggle/", views.site_toggle, name="site_toggle"),
    path("users/", views.users_list, name="users"),
    path("users/export.csv", views.users_export, name="users_export"),
    path("users/<int:user_id>/", views.user_detail, name="user_detail"),
    path("users/<int:user_id>/action/", views.user_action, name="user_action"),
    path("audit/", views.audit_log, name="audit"),
    path("ledger/", views.ledger, name="ledger"),
    path("ledger/export.csv", views.ledger_export, name="ledger_export"),
    path("moderation/", views.moderation, name="moderation"),
    path("moderation/action/", views.moderation_action, name="moderation_action"),
    path("health/", views.system_health, name="health"),
]
