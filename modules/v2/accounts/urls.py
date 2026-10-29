from django.urls import path

from .views import RegisterDisabledView, UserLoginView, UserLogoutView, connect_steam_view, security_view, two_factor_verify_view, view_as_user_enter_view, view_as_user_exit_view

app_name = "accounts"

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", RegisterDisabledView.as_view(), name="register"),
    path("security/", security_view, name="security"),
    path("2fa/", two_factor_verify_view, name="two_factor_verify"),
    path("view-as/user/", view_as_user_enter_view, name="view_as_user_enter"),
    path("view-as/exit/", view_as_user_exit_view, name="view_as_user_exit"),
    path("steam/connect/", connect_steam_view, name="connect_steam"),
]