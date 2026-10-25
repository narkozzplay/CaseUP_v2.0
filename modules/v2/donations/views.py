from __future__ import annotations

from decimal import ROUND_UP, Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.payments.services import get_topup_limits
from modules.v2.profiles.models import UserProfile
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import DonateForm
from .models import Donation
from .services import DonationError, current_goal, donate, new_token, widget_state_for

MY_HISTORY_SIZE = 10


def _initial_amount(request, goal) -> str:
    raw = (request.GET.get("amount") or "").strip()
    try:
        value = Decimal(raw)
    except Exception:  # noqa: BLE001 - a junk query string just leaves the field empty
        return ""
    return f"{value:.0f}" if goal.min_amount <= value <= goal.max_amount else ""


def donate_page(request):
    goal = current_goal()
    context = {
        "donation_state": widget_state_for("page"),
        "donation_goal": goal,
        "donation_token": new_token(),
        "donation_amount": _initial_amount(request, goal),
        "donation_balance": None,
        "my_donations": [],
    }
    if request.user.is_authenticated:
        profile = UserProfile.objects.filter(user=request.user).only("balance_coins").first()
        context["donation_balance"] = profile.balance_coins if profile else Decimal("0.00")
        context["my_donations"] = Donation.objects.filter(user=request.user)[:MY_HISTORY_SIZE]
    return render_dashboard_page(
        request,
        full_template="donations/page.html",
        partial_template="donations/partials/page_content.html",
        context=context,
        page_title=build_page_title(goal.title if goal.is_enabled else "Поддержать проект"),
        active_menu_key="donations",
    )


@login_required
@require_POST
def donate_submit(request):
    form = DonateForm(request.POST)
    page_url = reverse("donations:page")
    if not form.is_valid():
        messages.error(request, " ".join(str(error) for errors in form.errors.values() for error in errors) or "Проверьте сумму доната.")
        return redirect(page_url)
    amount = form.cleaned_data["amount"]
    try:
        donation = donate(
            user=request.user,
            amount=amount,
            token=form.cleaned_data["token"],
            message=form.cleaned_data.get("message") or "",
            is_anonymous=bool(form.cleaned_data.get("is_anonymous")),
        )
    except DonationError as exc:
        if "недостаточно" in str(exc):
            profile = UserProfile.objects.filter(user=request.user).only("balance_coins").first()
            shortfall = amount - (profile.balance_coins if profile else Decimal("0.00"))
            messages.warning(request, f"На балансе не хватает {shortfall:.2f} ₽. Пополните баланс, и вы вернётесь к донату.")
            topup_min, _topup_max = get_topup_limits()
            topup_amount = max(shortfall, topup_min).quantize(Decimal("1"), rounding=ROUND_UP)
            query = urlencode({"amount": f"{topup_amount:f}", "next": f"{page_url}?amount={amount:.0f}"})
            return redirect(f"{reverse('payments:topup')}?{query}")
        messages.error(request, str(exc))
        return redirect(page_url)
    goal = current_goal()
    messages.success(request, f"{goal.thank_you_text} Донат {donation.amount:.2f} ₽ списан с баланса.")
    return redirect(page_url)
