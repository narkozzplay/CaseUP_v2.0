from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from modules.v2.profiles.models import VerificationRequest
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .auth import get_farm_access


def _latest_farmer_request(user):
    return (
        VerificationRequest.objects
        .filter(user=user, requested_level=VerificationRequest.RequestedLevel.FARMER)
        .order_by("-created_at")
        .first()
    )


def render_farm_join_page(request):
    """What a user without farm access sees instead of a bare 403: what Farm is and how to get in."""
    return render_dashboard_page(
        request,
        full_template="farm/join.html",
        partial_template="farm/partials/pages/join_content.html",
        context={"farmer_request": _latest_farmer_request(request.user)},
        page_title=build_page_title("Ферма"),
        active_menu_key="farm_dashboard",
    )


@login_required
@require_POST
def farm_apply(request):
    if get_farm_access(request.user).has_access:
        return redirect("farm:dashboard")
    if VerificationRequest.objects.filter(user=request.user, status=VerificationRequest.Status.PENDING).exists():
        messages.info(request, "Заявка уже на рассмотрении. Мы напишем, когда проверим её.")
        return redirect("farm:dashboard")
    accounts = (request.POST.get("accounts") or "").strip()[:20]
    message = (request.POST.get("message") or "").strip()[:450]
    user_message = f"Аккаунтов: {accounts}\n{message}".strip() if accounts else message
    VerificationRequest.objects.create(
        user=request.user,
        requested_level=VerificationRequest.RequestedLevel.FARMER,
        user_message=user_message,
    )
    messages.success(request, "Заявка на доступ к Ферме отправлена. Модератор проверит её и откроет раздел.")
    return redirect("farm:dashboard")
