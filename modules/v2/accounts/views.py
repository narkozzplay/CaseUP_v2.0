from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView


class UserLoginView(TemplateView):
    template_name = "accounts/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("profiles:profile")
        return super().dispatch(request, *args, **kwargs)


class UserLogoutView(LogoutView):
    next_page = reverse_lazy("frontend:home")


class RegisterDisabledView(View):
    template_name = "accounts/register.html"

    def get(self, request):
        return render(request, self.template_name, status=403)

    def post(self, request):
        return render(request, self.template_name, status=403)