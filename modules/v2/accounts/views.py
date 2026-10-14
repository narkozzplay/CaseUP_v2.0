from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView
from social_django.views import auth as social_auth_begin

from .consent import store_login_consent
from .forms import SteamLoginConsentForm


class UserLoginView(TemplateView):
    template_name = "accounts/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("profiles:profile")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("consent_form", SteamLoginConsentForm())
        context["consent_required"] = self.request.GET.get("consent") == "required"
        context["next_url"] = _safe_next(self.request)
        return context

    def post(self, request, *args, **kwargs):
        form = SteamLoginConsentForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(consent_form=form))
        store_login_consent(request)
        # social-auth-app-django 6+ starts a login only on POST (login-CSRF protection), so a redirect
        # (which becomes a GET) would answer 405. This POST already passed CSRF; hand it over directly.
        # social-auth reads "next" from the same POST data.
        return social_auth_begin(request, "steam")


def _safe_next(request) -> str:
    candidate = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return ""


class UserLogoutView(LogoutView):
    next_page = reverse_lazy("frontend:home")


class RegisterDisabledView(View):
    template_name = "accounts/register.html"

    def get(self, request):
        return render(request, self.template_name, status=403)

    def post(self, request):
        return render(request, self.template_name, status=403)