from django.urls import path

from .views import admin_panel, agreement_page, cookies_page, dashboard_preview, home, pd_consent_page, privacy_page

app_name = "frontend"

urlpatterns = [
    path("", home, name="home"),
    path("dashboard-preview/", dashboard_preview, name="dashboard_preview"),
    path("agreement/", agreement_page, name="agreement"),
    path("privacy/", privacy_page, name="privacy"),
    path("personal-data-consent/", pd_consent_page, name="pd_consent"),
    path("cookies/", cookies_page, name="cookies"),
    path("control-center/", admin_panel, name="admin_panel"),
]