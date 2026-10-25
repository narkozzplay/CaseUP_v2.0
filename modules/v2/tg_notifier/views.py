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

from .channel_digest import CATEGORY_LABELS, TEXT_PLACEHOLDERS, build_preview_text, post_digest
from .forms import ChannelDigestSectionFormSet, TelegramChannelDigestForm, TelegramNotifierSettingsForm, TelegramTestSendForm
from .models import ChannelDigestPost, ChannelDigestSection, TelegramNotifierConfig
from .updates import get_poll_status, handle_update
from .services import (
    relay_settings,
    resolve_bot_token,
    TelegramNotifierError,
    build_test_message,
    build_webhook_url,
    get_telegram_notifier_config,
    get_webhook_info,
    register_webhook,
    send_message,
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


def _webhook_status(config, webhook_info) -> dict | None:
    """Telegram's view of the webhook in plain words (only after «Проверить приём»)."""
    if webhook_info is None:
        return None
    from datetime import datetime, timezone as dt_timezone

    polling = config.updates_mode == TelegramNotifierConfig.UpdatesMode.POLLING
    # In polling mode no webhook may be registered (the poller removes it by itself).
    expected = "" if polling else build_webhook_url(config)
    error_at = None
    if webhook_info.last_error_date:
        error_at = datetime.fromtimestamp(int(webhook_info.last_error_date), tz=dt_timezone.utc)
    return {
        "url": webhook_info.url,
        "matches": webhook_info.url == expected,
        "polling": polling,
        "pending": webhook_info.pending_update_count,
        "error": webhook_info.last_error_message,
        "error_at": error_at,
    }


def _poll_state(config) -> dict | None:
    """What the poller last reported: fresh = it ran within the last two minutes."""
    if config.updates_mode != TelegramNotifierConfig.UpdatesMode.POLLING:
        return None
    from django.utils import timezone

    status = get_poll_status()
    if not status:
        return {"ran": False}
    age = (timezone.now() - status["at"]).total_seconds()
    return {"ran": True, "at": status["at"], "fresh": age < 120, "error": status.get("error", "")}


def _build_settings_context(
    *, role_access, settings_form, test_form, config, webhook_info=None, bot_check=None
):
    from modules.v2.profiles.models import UserProfile

    return {
        "role_access": role_access,
        "settings_form": settings_form,
        "test_form": test_form,
        "config": config,
        "template_variables": TEMPLATE_VARIABLES,
        "webhook_url": build_webhook_url(config),
        "telegram_relay_url": relay_settings()[0],
        "webhook_info": webhook_info,
        "webhook_status": _webhook_status(config, webhook_info),
        "polling": config.updates_mode == TelegramNotifierConfig.UpdatesMode.POLLING,
        "poll_state": _poll_state(config),
        "bot_check": bot_check,
        "has_bot_token": bool(resolve_bot_token(config)),
        "channel_sections": ChannelDigestSection.objects.filter(is_enabled=True).count(),
        "linked_users": UserProfile.objects.exclude(telegram_chat_id__isnull=True).exclude(telegram_chat_id="").count(),
    }


@login_required
def settings_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("TG Notifier доступен только admin+.")

    config = get_telegram_notifier_config()
    webhook_info = None
    bot_check = None

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
                if config.updates_mode == TelegramNotifierConfig.UpdatesMode.POLLING:
                    raise TelegramNotifierError("включён режим «Опрос» — вебхук не нужен.")
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

        elif action == "check_bot":
            from modules.v2.system.services import _check_telegram

            settings_form = TelegramNotifierSettingsForm(instance=config)
            test_form = TelegramTestSendForm(initial={"chat_id": config.default_chat_id, "text": build_test_message()})
            token = resolve_bot_token(config)
            status, text = _check_telegram(token) if token else ("missing_key", "Токен бота не задан.")
            bot_check = {"ok": status == "up", "text": text}

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
        bot_check=bot_check,
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
def autopost_page(request):
    """Channel digest: schedule, sections (category, rise/fall, top N), preview, publish now and the post journal."""
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Автопостинг доступен только admin+.")

    config = get_telegram_notifier_config()
    sections = ChannelDigestSection.objects.all()
    preview = None
    schedule_form = TelegramChannelDigestForm(instance=config)
    section_formset = ChannelDigestSectionFormSet(queryset=sections, prefix="sections")

    if request.method == "POST":
        action = request.POST.get("action") or "save"
        # Every button works with what is on the screen, so the forms are saved first.
        schedule_form = TelegramChannelDigestForm(request.POST, instance=config)
        section_formset = ChannelDigestSectionFormSet(request.POST, queryset=sections, prefix="sections")
        if schedule_form.is_valid() and section_formset.is_valid():
            config = schedule_form.save()
            section_formset.save()
            if action == "preview":
                try:
                    preview = build_preview_text(config) or ""
                except TelegramNotifierError as exc:
                    messages.error(request, f"Предпросмотр не удался: {exc}")
                else:
                    if not preview:
                        messages.info(request, "За сутки нет заметных изменений среди предметов со свежими ценами — пост не выйдет.")
            elif action == "post_now":
                try:
                    preview = post_digest(config)
                except TelegramNotifierError as exc:
                    messages.error(request, f"Пост в канал не отправлен: {exc}")
                else:
                    messages.success(request, f"Сводка опубликована в {config.channel_chat_id}.")
            else:
                messages.success(request, "Настройки автопостинга сохранены.")
            schedule_form = TelegramChannelDigestForm(instance=config)
            section_formset = ChannelDigestSectionFormSet(queryset=ChannelDigestSection.objects.all(), prefix="sections")
        else:
            messages.error(request, "Исправьте ошибки в форме.")

    context = {
        "role_access": role_access,
        "config": config,
        "schedule_form": schedule_form,
        "section_formset": section_formset,
        "category_labels": CATEGORY_LABELS,
        "text_placeholders": TEXT_PLACEHOLDERS.items(),
        "preview": preview,
        "posts": ChannelDigestPost.objects.all()[:20],
        "bot_ready": config.is_enabled and bool(resolve_bot_token(config)),
    }
    return render_dashboard_page(
        request,
        full_template="tg_notifier/autopost.html",
        partial_template="tg_notifier/partials/autopost_content.html",
        context=context,
        page_title=build_page_title("Автопостинг в канал"),
        active_menu_key="tg_settings",
    )


def price_movers_page(request):
    """Public "Топ цен за сутки": the same sections as the channel digest, the page the post picture is made of."""
    from django.core.cache import cache
    from django.shortcuts import render

    from .channel_digest import card_context, collect_digest

    config = get_telegram_notifier_config()
    context = None
    try:
        context = cache.get(PRICE_MOVERS_CACHE_KEY)
    except Exception:  # noqa: BLE001 - without the cache the page is built fresh
        pass
    if context is None:
        context = card_context(collect_digest(config=config), config=config)
        try:
            cache.set(PRICE_MOVERS_CACHE_KEY, context, settings.STEAM_CATALOG_GUEST_CACHE_SECONDS)
        except Exception:  # noqa: BLE001
            pass
    context = {
        **context,
        "seo_title": f"Топ цен CS2 за сутки — что подорожало и подешевело | {context['site_name']}",
        "seo_description": "Кейсы, ножи и скины CS2, которые сильнее всего подорожали и подешевели в Steam за последние 24 часа. Цены, проценты и графики.",
        "seo_canonical": request.build_absolute_uri(request.path),
    }
    return render(request, "tg_notifier/movers.html", context)


PRICE_MOVERS_CACHE_KEY = "tg_notifier:price-movers"


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

    return JsonResponse(handle_update(payload))
