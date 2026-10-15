from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from modules.v2.control_center._compat import client_ip
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .models import DailyBonusClaim, DailyBonusReward
from .services import DailyBonusError, calendar, claim, eligibility, pending_free_cases

HISTORY_SIZE = 14


@login_required
def bonus_page(request):
    return render_dashboard_page(
        request,
        full_template="daily_bonus/page.html",
        partial_template="daily_bonus/partials/page_content.html",
        context={
            "bonus": calendar(request.user),
            "eligibility": eligibility(request.user),
            "free_cases": pending_free_cases(request.user),
            "history": DailyBonusClaim.objects.filter(user=request.user).select_related("case")[:HISTORY_SIZE],
        },
        page_title=build_page_title("Бонус дня"),
        active_menu_key="daily_bonus",
    )


@login_required
@require_POST
def bonus_claim(request):
    try:
        entry = claim(request.user, ip_address=client_ip(request))
    except DailyBonusError as exc:
        messages.error(request, str(exc))
    else:
        if entry.kind == DailyBonusReward.Kind.CASE:
            messages.success(request, f"День {entry.day}: бесплатное открытие кейса «{entry.case.name}» ждёт вас!")
        else:
            messages.success(request, f"День {entry.day}: +{entry.amount:.2f} ₽ на баланс. Серия — {entry.streak} дн.")
    return redirect("daily_bonus:page")
