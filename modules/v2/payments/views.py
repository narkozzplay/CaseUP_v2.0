from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from modules.v2.profiles.models import UserProfile
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from frontend.legal import AGREEMENT_VERSION

from .forms import TopupForm
from .ip_allowlist import is_allowed_webhook_source
from .models import Payment
from .providers import PaymentProviderError, get_provider
from .providers.dev import DevPaymentProvider
from .services import TopupError, create_topup_payment, get_topup_limits, get_topup_presets, handle_webhook, quote_topup, sync_payment, topup_fee_percent


logger = logging.getLogger(__name__)


RETURN_SESSION_KEY = "payments_return_to"
RETURN_PREFIXES = ("/market/", "/donate/")


def _safe_return_to(value: str) -> str:
    """Only our own market and donation pages may be returned to after a top-up (no open redirect).

    The pages may carry a language prefix (/en/donate/), since they live in i18n_patterns.
    """
    value = (value or "").strip()
    path = value
    for code, _name in settings.LANGUAGES:
        if path.startswith(f"/{code}/"):
            path = path[len(code) + 1:]
            break
    return value if path.startswith(RETURN_PREFIXES) and "//" not in value and "\\" not in value else ""


def _pending_topup_promo(user):
    from modules.v2.promo.models import PromoCode
    from modules.v2.promo.services import pending_activation

    return pending_activation(user, PromoCode.Kind.TOPUP_BONUS)


def _apply_promo(request):
    """The promo field on the top-up page: activate the code, or ask before it replaces the current one."""
    from modules.v2.control_center._compat import client_ip
    from modules.v2.promo.services import PromoError, PromoReplaceRequired, activate

    try:
        result = activate(
            user=request.user,
            raw_code=request.POST.get("code", ""),
            ip_address=client_ip(request),
            replace=request.POST.get("replace") == "1",
        )
    except PromoReplaceRequired as exc:
        return exc
    except PromoError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, result.message)
    return None


@login_required
def topup_view(request):
    provider = get_provider()
    data = None
    return_to = _safe_return_to(request.POST.get("next") or request.GET.get("next") or "")
    promo_replace = None
    if request.method == "POST" and request.POST.get("promo_action") == "apply":
        promo_replace = _apply_promo(request)
        if promo_replace is None:
            url = reverse("payments:topup")
            return redirect(f"{url}?{urlencode({'next': return_to})}" if return_to else url)
    elif request.method == "POST":
        data = request.POST.copy()
        if data.get("preset"):
            data["amount"] = data["preset"]
    form = TopupForm(data, initial={"amount": request.GET.get("amount") or None})

    if data is not None:
        if form.is_valid():
            try:
                payment = create_topup_payment(
                    user=request.user,
                    amount=form.cleaned_data["amount"],
                    terms_version=AGREEMENT_VERSION,
                    return_url_builder=lambda p: request.build_absolute_uri(
                        reverse("payments:return", args=[p.public_id])
                    ),
                )
            except TopupError as exc:
                messages.error(request, str(exc))
            else:
                if return_to:
                    request.session.setdefault(RETURN_SESSION_KEY, {})[str(payment.public_id)] = return_to
                    request.session.modified = True
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
        "topup_promo": _pending_topup_promo(request.user),
        "topup_promo_replace": promo_replace,
        "topup_fee_percent": topup_fee_percent(),
        "topup_preset_quotes": [(preset, quote_topup(preset)[1]) for preset in get_topup_presets()],
        "topup_next": return_to,
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

    return_to = (request.session.get(RETURN_SESSION_KEY) or {}).get(str(payment.public_id), "")
    if payment.status == Payment.Status.SUCCEEDED:
        messages.success(request, f"Баланс пополнен на {payment.balance_amount:.2f} ₽.")
        if return_to:
            request.session[RETURN_SESSION_KEY].pop(str(payment.public_id), None)
            request.session.modified = True
            return redirect(return_to)
    elif payment.status == Payment.Status.CANCELED:
        messages.warning(request, "Платёж отменён, деньги не списаны.")
    elif payment.status == Payment.Status.FAILED:
        messages.error(request, payment.error_message or "Платёж не прошёл.")
    else:
        messages.info(request, "Платёж ещё обрабатывается. Баланс обновится автоматически после подтверждения.")
    return redirect("payments:topup")


@login_required
def freekassa_return_view(request):
    """FreeKassa sends the payer to one fixed success/fail URL with the order id in the query."""
    order_id = request.GET.get("MERCHANT_ORDER_ID") or request.GET.get("o") or ""
    try:
        public_id = uuid.UUID(order_id)
    except ValueError:
        return redirect("payments:topup")
    if not Payment.objects.filter(public_id=public_id, user=request.user).exists():
        return redirect("payments:topup")
    return redirect("payments:return", public_id=public_id)


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
    provider = get_provider(provider_code)
    if provider is not None and not is_allowed_webhook_source(request, provider):
        return HttpResponse(status=403)
    if provider is None:
        return HttpResponse(status=200)
    try:
        payload = provider.parse_webhook(request)
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)

    try:
        handle_webhook(provider_code=provider_code, payload=payload)
    except PaymentProviderError as exc:
        # Non-2xx makes the provider retry later.
        logger.warning("Webhook %s could not be verified: %s", provider_code, exc)
        return HttpResponse(status=503)
    return HttpResponse(provider.webhook_ok_body, status=200, content_type="text/plain")
