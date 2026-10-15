from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import redirect
from django.urls import reverse

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .models import ReferralCode
from .services import COOKIE_NAME, SESSION_KEY, build_summary, cookie_days, find_code, get_or_create_code


def referral_link(request, code: str):
    """/r/<code>/ — remember who invited this visitor, then show the site."""
    response = redirect("frontend:home")
    ref_code = find_code(code)
    if ref_code is None or request.user.is_authenticated:
        return response
    ReferralCode.objects.filter(pk=ref_code.pk).update(clicks=F("clicks") + 1)
    response.set_cookie(
        COOKIE_NAME,
        ref_code.code,
        max_age=cookie_days() * 24 * 60 * 60,
        samesite="Lax",
        httponly=True,
        secure=request.is_secure(),
    )
    if hasattr(request, "session"):
        request.session[SESSION_KEY] = ref_code.code
    return response


@login_required
def referrals_dashboard(request):
    code = get_or_create_code(request.user)
    context = {
        "referral_code": code,
        "referral_url": request.build_absolute_uri(reverse("referral_link", args=[code.code])),
        **build_summary(request.user),
    }
    return render_dashboard_page(
        request,
        full_template="referrals/referrals.html",
        partial_template="referrals/partials/referrals_content.html",
        context=context,
        page_title=build_page_title("Рефералы"),
        active_menu_key="referrals",
    )
