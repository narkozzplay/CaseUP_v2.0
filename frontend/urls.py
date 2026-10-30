from django.urls import path

from .views import (
    admin_panel,
    agreement_page,
    cookies_page,
    dashboard_preview,
    home,
    home_live,
    pd_consent_page,
    pickem_rules_page,
    privacy_page,
    withdrawal_page,
)

app_name = "frontend"

urlpatterns = [
    path("", home, name="home"),
    path("live-feed/", home_live, name="home_live"),
    path("dashboard-preview/", dashboard_preview, name="dashboard_preview"),
    path("agreement/", agreement_page, name="agreement"),
    path("privacy/", privacy_page, name="privacy"),
    path("personal-data-consent/", pd_consent_page, name="pd_consent"),
    path("cookies/", cookies_page, name="cookies"),
    path("withdrawal-policy/", withdrawal_page, name="withdrawal"),
    path("pickem-rules/", pickem_rules_page, name="pickem_rules"),
    path("control-center/", admin_panel, name="admin_panel"),
]