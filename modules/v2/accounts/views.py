from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView
from social_django.views import auth as social_auth_begin

from modules.v2.control_center.models import ActivityCategory
from modules.v2.control_center.services.activity import log_activity
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .consent import store_login_consent
from .devices import DEVICE_LABELS
from .forms import SteamLoginConsentForm
from .geoip import ATTRIBUTION_URL
from .models import UserSession
from .sessions import active_sessions, end_other_sessions, end_session, recent_ended_sessions


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

@login_required
def security_view(request):
    """Profile → Безопасность: signed-in devices, ending one or all other sessions."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "end_others":
            count = end_other_sessions(request.user, keep_session_key=request.session.session_key)
            log_activity(category=ActivityCategory.SECURITY, event="account.sessions_ended_others", summary=f"Завершены другие сеансы: {count}", user=request.user)
            messages.success(request, f"Завершено сеансов: {count}." if count else "Других активных сеансов нет.")
        elif action == "end" and str(request.POST.get("session_id", "")).isdigit():
            target = UserSession.objects.filter(user=request.user, pk=int(request.POST["session_id"])).first()
            if target is not None and target.session_key == request.session.session_key:
                messages.error(request, "Это текущий сеанс. Чтобы завершить его, просто выйдите из аккаунта.")
            elif target is not None and end_session(request.user, target.pk):
                log_activity(category=ActivityCategory.SECURITY, event="account.session_ended", summary=f"Завершён сеанс: {target.device_label}", user=request.user, obj=target, payload={"ip": target.last_ip or target.ip_address})
                messages.success(request, f"Сеанс «{target.device_label}» завершён.")
            else:
                messages.error(request, "Сеанс уже завершён.")
        return redirect("accounts:security")

    sessions = list(active_sessions(request.user))
    current_key = request.session.session_key
    sessions.sort(key=lambda record: record.session_key != current_key)
    return render_dashboard_page(
        request,
        full_template="accounts/security.html",
        partial_template="accounts/partials/security_content.html",
        context={
            "active_sessions": sessions,
            "current_session_key": current_key,
            "ended_sessions": recent_ended_sessions(request.user),
            "device_labels": DEVICE_LABELS,
            "geoip_attribution_url": ATTRIBUTION_URL,
        },
        page_title=build_page_title("Безопасность"),
        active_menu_key="profile",
    )
