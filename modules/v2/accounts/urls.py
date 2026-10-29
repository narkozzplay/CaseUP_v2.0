from django.urls import path

from .views import RegisterDisabledView, UserLoginView, UserLogoutView, connect_steam_view, security_view, two_factor_verify_view, view_as_user_exit, view_as_user_start

app_name = "accounts"

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", RegisterDisabledView.as_view(), name="register"),
    path("security/", security_view, name="security"),
    path("2fa/", two_factor_verify_view, name="two_factor_verify"),
    path("view-as-user/", view_as_user_start, name="view_as_user_start"),
    path("view-as-user/exit/", view_as_user_exit, name="view_as_user_exit"),
    path("steam/connect/", connect_steam_view, name="connect_steam"),
]