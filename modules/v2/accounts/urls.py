from django.urls import path

from .views import RegisterDisabledView, UserLoginView, UserLogoutView, security_view

app_name = "accounts"

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", RegisterDisabledView.as_view(), name="register"),
    path("security/", security_view, name="security"),
]