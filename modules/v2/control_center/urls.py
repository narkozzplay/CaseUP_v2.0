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
    path("business/", views.business, name="business"),
    path("business/revenue/", views.business_revenue, name="business_revenue"),
    path("business/revenue/export.csv", views.business_revenue_export, name="business_revenue_export"),
    path("ledger/export.csv", views.ledger_export, name="ledger_export"),
    path("payments/", views.payments, name="payments"),
    path("payments/export.csv", views.payments_export, name="payments_export"),
    path("payments/<int:payment_id>/recheck/", views.payment_recheck, name="payment_recheck"),
    path("activity/", views.activity_log, name="activity"),
    path("activity/export.csv", views.activity_log_export, name="activity_export"),
    path("referrals/", views.referrals, name="referrals"),
    path("referrals/<int:referral_id>/review/", views.referral_review, name="referral_review"),
    path("referrals/settings/", views.referral_settings, name="referral_settings"),
    path("promo/", views.promo_codes, name="promo"),
    path("daily-bonus/", views.daily_bonus, name="daily_bonus"),
    path("donations/", views.donations, name="donations"),
    path("market/", views.market_deals, name="market"),
    path("promo/new/", views.promo_code_edit, name="promo_create"),
    path("promo/<int:promo_id>/", views.promo_code_edit, name="promo_edit"),
    path("promo/activations/<int:activation_id>/cancel/", views.promo_activation_cancel, name="promo_activation_cancel"),
    path("multi-accounts/", views.multi_accounts, name="multi_accounts"),
    path("multi-accounts/<int:case_id>/", views.multi_account_detail, name="multi_account_detail"),
    path("multi-accounts/<int:case_id>/decide/", views.multi_account_decide, name="multi_account_decide"),
    path("ip-bans/new/", views.ip_ban_create, name="ip_ban_create"),
    path("ip-bans/<int:ban_id>/lift/", views.ip_ban_lift, name="ip_ban_lift"),
    path("moderation/", views.moderation, name="moderation"),
    path("moderation/action/", views.moderation_action, name="moderation_action"),
    path("health/", views.system_health, name="health"),
    path("errors/", views.error_journal, name="errors"),
    path("errors/action/", views.error_journal_action, name="errors_action"),
]
