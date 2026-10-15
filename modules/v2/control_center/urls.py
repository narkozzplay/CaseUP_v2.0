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
    path("payments/", views.payments, name="payments"),
    path("payments/export.csv", views.payments_export, name="payments_export"),
    path("payments/<int:payment_id>/recheck/", views.payment_recheck, name="payment_recheck"),
    path("activity/", views.activity_log, name="activity"),
    path("activity/export.csv", views.activity_log_export, name="activity_export"),
    path("referrals/", views.referrals, name="referrals"),
    path("referrals/<int:referral_id>/review/", views.referral_review, name="referral_review"),
    path("moderation/", views.moderation, name="moderation"),
    path("moderation/action/", views.moderation_action, name="moderation_action"),
    path("health/", views.system_health, name="health"),
]
