from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from social_django.views import auth as social_auth_begin

from modules.v2.control_center.models import ActivityCategory
from modules.v2.control_center.services.activity import log_activity
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from . import two_factor
from .consent import store_login_consent, user_has_current_consent
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


_LOCKED_MESSAGE = f"Слишком много неверных кодов. Попробуйте снова через {int(two_factor.FAILURE_WINDOW.total_seconds() // 60)} мин."


def two_factor_verify_view(request):
    """Second login step: the session exists but is unusable until the authenticator code is entered."""
    next_url = _safe_next(request)
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if not two_factor.is_pending(request):
        return redirect(next_url or "profiles:profile")

    error = ""
    if request.method == "POST":
        try:
            method = two_factor.verify(request.user, request.POST.get("code", ""), request=request, context="вход")
        except two_factor.TwoFactorLocked:
            method = None
            error = _LOCKED_MESSAGE
        if method:
            two_factor.complete_pending(request)
            if method == two_factor.METHOD_BACKUP:
                record = two_factor.get_record(request.user)
                left = record.backup_codes_left if record else 0
                messages.warning(request, f"Вы вошли по резервному коду. Осталось кодов: {left}. Если телефон потерян — создайте новые коды в разделе «Безопасность».")
            return redirect(next_url or "profiles:profile")
        error = error or "Неверный код. Проверьте время на телефоне и введите код ещё раз."
    return render(
        request,
        "accounts/two_factor_verify.html",
        {"next_url": next_url, "error": error, "locked": two_factor.is_locked(request.user)},
        status=200,
    )


def _handle_two_factor_action(request, action: str) -> None:
    user = request.user
    code = request.POST.get("code", "")
    try:
        if action == "2fa_start":
            two_factor.start_setup(user)
        elif action == "2fa_cancel":
            two_factor.cancel_setup(user)
        elif action == "2fa_confirm":
            codes = two_factor.confirm_setup(user, code, request=request)
            if codes:
                request.session[two_factor.NEW_BACKUP_CODES_KEY] = codes
                messages.success(request, "Двухфакторная защита включена. Сохраните резервные коды.")
            else:
                messages.error(request, "Код не подошёл. Отсканируйте QR-код заново или проверьте время на телефоне.")
        elif action == "2fa_backup":
            if two_factor.verify(user, code, allow_backup=False, request=request, context="новые резервные коды"):
                request.session[two_factor.NEW_BACKUP_CODES_KEY] = two_factor.regenerate_backup_codes(user, request=request)
                messages.success(request, "Созданы новые резервные коды, старые больше не действуют.")
            else:
                messages.error(request, "Неверный код из приложения.")
        elif action == "2fa_disable":
            if two_factor.is_required(user):
                messages.error(request, "Для вашей роли двухфакторная защита обязательна, отключить её нельзя.")
            elif two_factor.verify(user, code, request=request, context="отключение"):
                two_factor.disable(user, request=request)
                messages.success(request, "Двухфакторная защита отключена.")
            else:
                messages.error(request, "Неверный код.")
    except two_factor.TwoFactorLocked:
        messages.error(request, _LOCKED_MESSAGE)


def _two_factor_context(request) -> dict:
    record = two_factor.get_record(request.user)
    required = two_factor.is_required(request.user)
    context = {
        "two_factor": record,
        "two_factor_enabled": bool(record and record.is_enabled),
        "two_factor_required": required,
        "two_factor_required_notice": required and not (record and record.is_enabled),
        "new_backup_codes": request.session.pop(two_factor.NEW_BACKUP_CODES_KEY, None),
    }
    if record is not None and not record.is_enabled:
        context["two_factor_qr"] = two_factor.qr_data_uri(two_factor.provisioning_uri(record, request.user))
        context["two_factor_secret"] = two_factor.format_secret(record.secret)
    return context


@login_required
def security_view(request):
    """Profile → Безопасность: two-factor authentication and signed-in devices."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action.startswith("2fa_"):
            _handle_two_factor_action(request, action)
        elif action == "end_others":
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
            **_two_factor_context(request),
        },
        page_title=build_page_title("Безопасность"),
        active_menu_key="profile",
    )


@login_required
@require_POST
def connect_steam_view(request):
    """Link Steam to an account that was created without it (the «Steam» card on the profile).

    Users who have not accepted the current documents tick the same two boxes as on the login page.
    """
    from .steam_sync import get_steam_social_auth

    if get_steam_social_auth(request.user) is not None:
        messages.info(request, "Steam уже подключён к этому аккаунту.")
        return redirect("profiles:profile")
    if not user_has_current_consent(request.user):
        form = SteamLoginConsentForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Чтобы подключить Steam, примите Пользовательское соглашение и согласие на обработку данных.")
            return redirect("profiles:profile")
        store_login_consent(request)
    # Same hand-over as the login page: social-auth starts only on POST and reads "next" from it.
    return social_auth_begin(request, "steam")
