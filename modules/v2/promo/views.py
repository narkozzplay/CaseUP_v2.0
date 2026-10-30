from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from modules.v2.control_center._compat import client_ip
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .models import PromoActivation
from .services import PromoError, PromoReplaceRequired, activate, normalize_code


@login_required
def promo_dashboard(request):
    """Profile → Промокоды: enter a code, see what is waiting to be used and what was used."""
    replace_prompt = None
    if request.method == "POST":
        try:
            result = activate(
                user=request.user,
                raw_code=request.POST.get("code", ""),
                ip_address=client_ip(request),
                replace=request.POST.get("replace") == "1",
            )
        except PromoReplaceRequired as exc:
            replace_prompt = exc
        except PromoError as exc:
            messages.error(request, str(exc))
        else:
            message = result.message
            if result.became_referral_of is not None:
                message += " Вы присоединились по приглашению автора промокода."
            messages.success(request, message)
        if replace_prompt is None:
            return redirect("promo:dashboard")

    activations = list(
        PromoActivation.objects.filter(user=request.user).select_related("promo", "promo__case").order_by("-created_at", "-id")
    )
    return render_dashboard_page(
        request,
        full_template="promo/promo.html",
        partial_template="promo/partials/promo_content.html",
        context={
            "activations": activations,
            "pending": [item for item in activations if item.is_usable],
            "prefill_code": normalize_code(request.GET.get("code", "")),
            "replace_prompt": replace_prompt,
        },
        page_title=build_page_title("Промокоды"),
        active_menu_key="promo",
    )
