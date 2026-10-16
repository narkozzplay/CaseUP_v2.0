from __future__ import annotations

import json
from secrets import compare_digest

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt

from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title
from modules.v2.steam_catalog.models import PriceDropAlertEvent, PriceDropAlertRule, UserPriceAlertEvent, UserPriceAlertRule
from modules.v2.system.services import get_module_state_map
from modules.v2.subscriptions.feature_codes import TG_NOTIFIER_USER
from modules.v2.subscriptions.responses import render_feature_locked_response
from modules.v2.subscriptions.services import get_feature_access_state

from .forms import TelegramNotifierSettingsForm, TelegramTestSendForm
from .services import (
    relay_settings,
    TelegramNotifierError,
    bind_profile_by_telegram_start,
    build_test_message,
    build_webhook_url,
    extract_start_token_from_message_text,
    get_telegram_notifier_config,
    get_webhook_info,
    register_webhook,
    send_message,
    touch_profile_last_interaction_by_chat_id,
)


TEMPLATE_VARIABLES = [
    ("trade_lock_label", "Статус трейд-бана: снят / в бане"),
    ("item_name", "Название предмета"),
    ("item_url", "Абсолютная ссылка на предмет"),
    ("purchased_at", "Дата покупки в формате dd.mm.YYYY HH:MM"),
    ("unlock_at", "Дата разблокировки в формате dd.mm.YYYY HH:MM"),
    ("quantity", "Количество активного остатка"),
    ("unit_buy_price", "Цена покупки за единицу"),
    ("total_spent", "Затрачено на активный остаток"),
    ("current_price", "Текущая цена за единицу"),
    ("current_value", "Текущая стоимость активного остатка"),
    ("current_value_after_fee", "Стоимость после комиссии Steam 15%"),
    ("profit_abs", "Абсолютная прибыль"),
    ("profit_pct", "Процент прибыли"),
    ("profit_after_fee", "Прибыль после комиссии"),
    ("hold_status_label", "готовы к продаже / в холде"),
]

NOTIFICATION_TYPES = [
    {
        "code": "steam_trading_unlock",
        "title": "Разблокировка Steam-покупок",
        "description": "Когда предмет выходит из трейд-бана, приходит уведомление о готовности к продаже.",
        "delivery": "В привязанный Telegram пользователя.",
    },
    {
        "code": "steam_trading_manual",
        "title": "Ручная отправка из Steam Investments",
        "description": "Кнопка TG в инвестициях отправляет карточку по конкретной покупке без Celery.",
        "delivery": "В привязанный Telegram пользователя.",
    },
    {
        "code": "steam_catalog_price_drop",
        "title": "Price movement alerts",
        "description": "Срабатывают по активным правилам PriceDropAlertRule при росте/падении цены.",
        "delivery": "Во все привязанные Telegram-чаты. Если их нет — в default chat id.",
    },
]


def _build_settings_context(*, role_access, settings_form, test_form, config, webhook_info=None):
    return {
        "role_access": role_access,
        "settings_form": settings_form,
        "test_form": test_form,
        "config": config,
        "template_variables": TEMPLATE_VARIABLES,
        "webhook_url": build_webhook_url(config),
        "telegram_relay_url": relay_settings()[0],
        "webhook_info": webhook_info,
    }


@login_required
def settings_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("TG Notifier доступен только admin+.")

    config = get_telegram_notifier_config()
    webhook_info = None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_settings":
            settings_form = TelegramNotifierSettingsForm(request.POST, instance=config)
            test_form = TelegramTestSendForm(
                initial={
                    "chat_id": config.default_chat_id,
                    "text": build_test_message(),
                }
            )

            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, "Настройки TG notifier сохранены.")
                if request.headers.get("X-Dashboard-Partial") == "1":
                    context = _build_settings_context(
                        role_access=role_access,
                        settings_form=TelegramNotifierSettingsForm(instance=config),
                        test_form=TelegramTestSendForm(
                            initial={
                                "chat_id": config.default_chat_id,
                                "text": build_test_message(),
                            }
                        ),
                        config=config,
                        webhook_info=None,
                    )
                    return render_dashboard_page(
                        request,
                        full_template="tg_notifier/settings.html",
                        partial_template="tg_notifier/partials/settings_content.html",
                        context=context,
                        page_title=build_page_title("TG Notifier"),
                        active_menu_key="tg_settings",
                        current_url=request.path,
                    )
                return redirect("tg_notifier:settings")

        elif action == "test_send":
            settings_form = TelegramNotifierSettingsForm(instance=config)
            test_form = TelegramTestSendForm(request.POST)

            if test_form.is_valid():
                try:
                    result = send_message(
                        text=test_form.cleaned_data["text"],
                        chat_id=(test_form.cleaned_data.get("chat_id") or "").strip() or None,
                    )
                except TelegramNotifierError as exc:
                    messages.error(request, f"Тестовая отправка не удалась: {exc}")
                else:
                    messages.success(request, f"Тестовое сообщение отправлено в chat id {result.chat_id}.")
                    if request.headers.get("X-Dashboard-Partial") == "1":
                        context = _build_settings_context(
                            role_access=role_access,
                            settings_form=settings_form,
                            test_form=TelegramTestSendForm(
                                initial={
                                    "chat_id": config.default_chat_id,
                                    "text": build_test_message(),
                                }
                            ),
                            config=config,
                            webhook_info=None,
                        )
                        return render_dashboard_page(
                            request,
                            full_template="tg_notifier/settings.html",
                            partial_template="tg_notifier/partials/settings_content.html",
                            context=context,
                            page_title=build_page_title("TG Notifier"),
                            active_menu_key="tg_settings",
                            current_url=request.path,
                        )
                    return redirect("tg_notifier:settings")

        elif action == "register_webhook":
            settings_form = TelegramNotifierSettingsForm(instance=config)
            test_form = TelegramTestSendForm(
                initial={
                    "chat_id": config.default_chat_id,
                    "text": build_test_message(),
                }
            )
            try:
                response_payload = register_webhook(config=config)
            except TelegramNotifierError as exc:
                messages.error(request, f"Регистрация webhook не удалась: {exc}")
            else:
                messages.success(
                    request,
                    f"Webhook зарегистрирован. Ответ Telegram: {response_payload.get('description', 'ok')}",
                )
                if request.headers.get("X-Dashboard-Partial") == "1":
                    context = _build_settings_context(
                        role_access=role_access,
                        settings_form=settings_form,
                        test_form=test_form,
                        config=config,
                        webhook_info=None,
                    )
                    return render_dashboard_page(
                        request,
                        full_template="tg_notifier/settings.html",
                        partial_template="tg_notifier/partials/settings_content.html",
                        context=context,
                        page_title=build_page_title("TG Notifier"),
                        active_menu_key="tg_settings",
                        current_url=request.path,
                    )
                return redirect("tg_notifier:settings")

        elif action == "check_webhook":
            settings_form = TelegramNotifierSettingsForm(instance=config)
            test_form = TelegramTestSendForm(
                initial={
                    "chat_id": config.default_chat_id,
                    "text": build_test_message(),
                }
            )
            try:
                webhook_info = get_webhook_info(config=config)
            except TelegramNotifierError as exc:
                messages.error(request, f"Проверка webhook не удалась: {exc}")

        else:
            settings_form = TelegramNotifierSettingsForm(instance=config)
            test_form = TelegramTestSendForm(
                initial={
                    "chat_id": config.default_chat_id,
                    "text": build_test_message(),
                }
            )
            messages.error(request, "Неизвестное действие формы.")
    else:
        settings_form = TelegramNotifierSettingsForm(instance=config)
        test_form = TelegramTestSendForm(
            initial={
                "chat_id": config.default_chat_id,
                "text": build_test_message(),
            }
        )

    context = _build_settings_context(
        role_access=role_access,
        settings_form=settings_form,
        test_form=test_form,
        config=config,
        webhook_info=webhook_info,
    )
    return render_dashboard_page(
        request,
        full_template="tg_notifier/settings.html",
        partial_template="tg_notifier/partials/settings_content.html",
        context=context,
        page_title=build_page_title("TG Notifier"),
        active_menu_key="tg_settings",
    )


@login_required
def notification_center_page(request):
    if not get_feature_access_state(request.user, TG_NOTIFIER_USER).is_available:
        return render_feature_locked_response(
            request,
            feature_code=TG_NOTIFIER_USER,
            title="Центр уведомлений закрыт тарифом",
            description="Пользовательские функции TG Notifier открываются начиная с подписки Level 3.",
        )

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    config = get_telegram_notifier_config()
    module_state_map = get_module_state_map()
    role_access = get_user_role(request.user)

    active_rules = list(
        PriceDropAlertRule.objects
        .filter(is_enabled=True)
        .order_by("item_type", "id")
    )
    user_rules = list(
        UserPriceAlertRule.objects.filter(user=request.user)
        .order_by("-updated_at", "-id")[:10]
    )

    recent_price_alert_events = list(
        PriceDropAlertEvent.objects
        .select_related("item", "rule")
        .order_by("-created_at", "-id")[:15]
    )
    recent_user_price_alert_events = list(
        UserPriceAlertEvent.objects.filter(user=request.user)
        .select_related("item", "rule")
        .order_by("-created_at", "-id")[:15]
    )

    context = {
        "role_access": role_access,
        "profile": profile,
        "config": config,
        "tg_module_enabled": bool(module_state_map.get("tg_notifier", False)),
        "notification_types": NOTIFICATION_TYPES,
        "active_rules": active_rules,
        "recent_price_alert_events": recent_price_alert_events,
        "user_rules": user_rules,
        "recent_user_price_alert_events": recent_user_price_alert_events,
    }
    return render_dashboard_page(
        request,
        full_template="tg_notifier/notification_center.html",
        partial_template="tg_notifier/partials/notification_center_content.html",
        context=context,
        page_title=build_page_title("Notification Center"),
        active_menu_key="notification_center",
    )


@csrf_exempt
def telegram_webhook_view(request, webhook_secret: str):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    raw_max_body_bytes = getattr(settings, "TELEGRAM_WEBHOOK_MAX_BODY_BYTES", 262_144)
    try:
        max_body_bytes = int(raw_max_body_bytes or 262_144)
    except (TypeError, ValueError):
        max_body_bytes = 262_144
    if max_body_bytes <= 0:
        max_body_bytes = 262_144
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length:
        try:
            if int(content_length) > max_body_bytes:
                return JsonResponse({"ok": False, "error": "payload_too_large"}, status=413)
        except (TypeError, ValueError):
            pass

    config = get_telegram_notifier_config()
    configured_secret = str(config.webhook_secret or "")
    if not configured_secret or not compare_digest(str(webhook_secret or ""), configured_secret):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    header_secret = str(request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
    if not header_secret or not compare_digest(header_secret, configured_secret):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    raw_body = request.body or b""
    if len(raw_body) > max_body_bytes:
        return JsonResponse({"ok": False, "error": "payload_too_large"}, status=413)

    try:
        raw_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_encoding"}, status=400)

    try:
        payload = json.loads(raw_text or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    message = payload.get("message") or {}
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}

    chat_id = str(chat.get("id") or "").strip()
    text = (message.get("text") or "").strip()

    if not chat_id:
        return JsonResponse({"ok": True, "ignored": "no_chat_id"})

    start_token = extract_start_token_from_message_text(text)
    if start_token:
        try:
            bind_profile_by_telegram_start(
                start_token=start_token,
                chat_id=chat_id,
                telegram_username=str(from_user.get("username") or "").strip(),
                telegram_first_name=str(from_user.get("first_name") or "").strip(),
            )
        except TelegramNotifierError as exc:
            try:
                send_message(
                    chat_id=chat_id,
                    text=f"❌ Не удалось подключить Telegram к профилю CaseUP: {exc}",
                )
            except TelegramNotifierError:
                pass
            return JsonResponse({"ok": True, "bound": False, "error": str(exc)})

        warning = ""
        try:
            send_message(
                chat_id=chat_id,
                text="✅ Telegram успешно подключен к вашему профилю CaseUP.",
            )
        except TelegramNotifierError as exc:
            warning = str(exc)

        if warning:
            return JsonResponse({"ok": True, "bound": True, "warning": warning})
        return JsonResponse({"ok": True, "bound": True})

    touch_profile_last_interaction_by_chat_id(chat_id)
    return JsonResponse({"ok": True, "ignored": "no_start_token"})
