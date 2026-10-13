from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from modules.v2.profiles.models import UserProfile
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import TopupForm
from .models import Payment
from .providers import PaymentProviderError, get_provider
from .providers.dev import DevPaymentProvider
from .services import TopupError, create_topup_payment, get_topup_limits, get_topup_presets, handle_webhook, sync_payment


logger = logging.getLogger(__name__)


@login_required
def topup_view(request):
    provider = get_provider()
    form = TopupForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            try:
                payment = create_topup_payment(
                    user=request.user,
                    amount=form.cleaned_data["amount"],
                    return_url_builder=lambda p: request.build_absolute_uri(
                        reverse("payments:return", args=[p.public_id])
                    ),
                )
            except TopupError as exc:
                messages.error(request, str(exc))
            else:
                if payment.status == Payment.Status.SUCCEEDED or not payment.confirmation_url:
                    return redirect("payments:return", public_id=payment.public_id)
                return redirect(payment.confirmation_url)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    minimum, maximum = get_topup_limits()
    context = {
        "topup_form": form,
        "topup_provider": provider,
        "topup_presets": get_topup_presets(),
        "topup_min": minimum,
        "topup_max": maximum,
        "topup_balance": profile.balance_coins,
        "topup_payments": Payment.objects.filter(user=request.user).order_by("-created_at", "-id")[:10],
    }
    return render_dashboard_page(
        request,
        full_template="payments/topup.html",
        partial_template="payments/partials/topup_content.html",
        context=context,
        page_title=build_page_title("Пополнение баланса"),
        active_menu_key="balance_topup",
    )


@login_required
def payment_return_view(request, public_id):
    payment = get_object_or_404(Payment, public_id=public_id, user=request.user)
    try:
        payment = sync_payment(payment)
    except PaymentProviderError as exc:
        logger.warning("Top-up %s sync failed on return: %s", payment.public_id, exc)

    if payment.status == Payment.Status.SUCCEEDED:
        messages.success(request, f"Баланс пополнен на {payment.amount:.2f} ₽.")
    elif payment.status == Payment.Status.CANCELED:
        messages.warning(request, "Платёж отменён, деньги не списаны.")
    elif payment.status == Payment.Status.FAILED:
        messages.error(request, payment.error_message or "Платёж не прошёл.")
    else:
        messages.info(request, "Платёж ещё обрабатывается. Баланс обновится автоматически после подтверждения.")
    return redirect("payments:topup")


@login_required
def payment_recheck_view(request, public_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    return payment_return_view(request, public_id)


@login_required
def dev_checkout_view(request, public_id):
    """Fake payment page for local development (provider "dev", DEBUG only)."""
    if not DevPaymentProvider.is_configured():
        raise Http404
    payment = get_object_or_404(Payment, public_id=public_id, user=request.user, provider=DevPaymentProvider.code)

    if request.method == "POST" and not payment.is_final:
        outcome = request.POST.get("outcome")
        dev_status = Payment.Status.SUCCEEDED if outcome == "pay" else Payment.Status.CANCELED
        payment.provider_payload = {**(payment.provider_payload or {}), "dev_status": dev_status}
        payment.save(update_fields=["provider_payload", "updated_at"])
        return redirect("payments:return", public_id=payment.public_id)

    return render_dashboard_page(
        request,
        full_template="payments/dev_checkout.html",
        partial_template="payments/partials/dev_checkout_content.html",
        context={"payment": payment},
        page_title=build_page_title("Тестовая оплата"),
        active_menu_key="balance_topup",
    )


@csrf_exempt
def webhook_view(request, provider_code: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)

    try:
        handle_webhook(provider_code=provider_code, payload=payload)
    except PaymentProviderError as exc:
        # Non-2xx makes the provider retry later.
        logger.warning("Webhook %s could not be verified: %s", provider_code, exc)
        return HttpResponse(status=503)
    return HttpResponse(status=200)
