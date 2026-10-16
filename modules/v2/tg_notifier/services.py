from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.db import transaction
from django.template import Context, Engine
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.steam_trading.investment_services import STATUS_READY, build_investment_row
from modules.v2.steam_trading.models import SteamPurchase
from modules.v2.steam_trading.services import calculate_steam_net_amount, quantize_money

from .models import DEFAULT_INVESTMENT_MESSAGE_TEMPLATE, TelegramLinkToken, TelegramNotifierConfig


class TelegramNotifierError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramSendResult:
    chat_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class TelegramWebhookBindResult:
    profile_id: int
    chat_id: str
    telegram_username: str
    telegram_first_name: str

@dataclass(frozen=True)
class TelegramWebhookInfoResult:
    url: str
    pending_update_count: int
    last_error_date: int | None
    last_error_message: str
    max_connections: int | None
    ip_address: str
    has_custom_certificate: bool


def get_telegram_notifier_config() -> TelegramNotifierConfig:
    config, _created = TelegramNotifierConfig.objects.get_or_create(
        singleton_key=1,
        defaults=TelegramNotifierConfig.bootstrap_defaults(),
    )
    return config


def resolve_bot_token(config: TelegramNotifierConfig | None = None) -> str:
    config = config or get_telegram_notifier_config()
    from modules.v2.system.services import get_integration_secret

    return (config.bot_token or get_integration_secret("telegram_bot_token") or "").strip()


def resolve_default_chat_id(config: TelegramNotifierConfig | None = None) -> str:
    config = config or get_telegram_notifier_config()
    return (config.default_chat_id or settings.TELEGRAM_CHAT_ID or "").strip()


def is_enabled(config: TelegramNotifierConfig | None = None) -> bool:
    from modules.v2.system.models import ModuleState

    config = config or get_telegram_notifier_config()

    module_state = (
        ModuleState.objects
        .filter(key="tg_notifier")
        .values_list("is_enabled", flat=True)
        .first()
    )

    if module_state is None:
        return bool(config.is_enabled)

    return bool(module_state and config.is_enabled)


def resolve_public_base_url(config: TelegramNotifierConfig | None = None) -> str:
    from modules.v2.system.services import get_public_base_url

    config = config or get_telegram_notifier_config()

    return (
        (config.public_base_url or "").strip()
        or (get_public_base_url() or "").strip()
        or (settings.SITE_BASE_URL or "").strip()
    ).rstrip("/")


def ensure_public_base_url(config: TelegramNotifierConfig | None = None) -> str:
    base_url = resolve_public_base_url(config)
    if not base_url:
        raise TelegramNotifierError("Public base URL не настроен.")
    if not (base_url.startswith("https://") or base_url.startswith("http://")):
        raise TelegramNotifierError("Public base URL должен начинаться с http:// или https://.")
    return base_url

def _build_telegram_endpoint(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def relay_settings() -> tuple[str, str]:
    """Telegram is blocked from Russian servers: requests can go through a relay on other hosting
    (deploy/hosting/telegram_relay.php). Returns (relay URL, relay key); an empty URL means direct."""
    url = (getattr(settings, "TELEGRAM_RELAY_URL", "") or "").strip()
    token = (getattr(settings, "TELEGRAM_RELAY_TOKEN", "") or getattr(settings, "STEAM_PRICE_RELAY_TOKEN", "") or "").strip()
    return url, token


_ENDPOINT_RE = re.compile(r"^https://api\.telegram\.org/bot(?P<token>[^/]+)/(?P<method>[A-Za-z]+)$")


def _route(endpoint: str) -> tuple[str, dict[str, str]]:
    relay_url, relay_token = relay_settings()
    match = _ENDPOINT_RE.match(endpoint)
    if not relay_url or not match:
        return endpoint, {}
    separator = "&" if "?" in relay_url else "?"
    return f"{relay_url}{separator}method={match['method']}", {"X-Relay-Token": relay_token, "X-Bot-Token": match["token"]}


def _telegram_api_request(*, endpoint: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    raw_payload = json.dumps(payload).encode("utf-8")
    url, extra_headers = _route(endpoint)
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={"Content-Type": "application/json; charset=utf-8", **extra_headers},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        raise TelegramNotifierError(f"Telegram API HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise TelegramNotifierError(f"Telegram API connection error: {exc}") from exc
    except TimeoutError as exc:
        raise TelegramNotifierError("Telegram API timeout.") from exc

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TelegramNotifierError("Telegram API returned invalid JSON.") from exc

    if not data.get("ok"):
        raise TelegramNotifierError(f"Telegram API error: {data}")

    return data


def send_message(
    *,
    text: str,
    chat_id: str | None = None,
    timeout: int = 15,
    parse_mode: str | None = None,
) -> TelegramSendResult:
    config = get_telegram_notifier_config()

    if not is_enabled(config):
        raise TelegramNotifierError("TG notifier выключен в настройках.")

    bot_token = resolve_bot_token(config)
    if not bot_token:
        raise TelegramNotifierError("Bot token не настроен.")

    resolved_chat_id = (chat_id or resolve_default_chat_id(config) or "").strip()
    if not resolved_chat_id:
        raise TelegramNotifierError("Default chat id не настроен.")

    payload = {
        "chat_id": resolved_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    response_payload = _telegram_api_request(
        endpoint=_build_telegram_endpoint(bot_token, "sendMessage"),
        payload=payload,
        timeout=timeout,
    )
    return TelegramSendResult(chat_id=resolved_chat_id, payload=response_payload)


def build_item_absolute_url(purchase: SteamPurchase) -> str:
    return f"{ensure_public_base_url()}{reverse('steam_catalog:detail', args=[purchase.item_id])}"


def format_datetime_msk(value) -> str:
    if not value:
        return "—"
    return timezone.localtime(value).strftime("%d.%m.%Y %H:%M")


def format_money(value: Decimal | int | float | str | None) -> str:
    return f"{quantize_money(value):.2f} ₽"


def format_signed_money(value: Decimal | int | float | str | None) -> str:
    value = quantize_money(value)
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f} ₽"


def format_signed_percent(value: Decimal | int | float | str | None) -> str:
    value = quantize_money(value)
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def build_investment_message_context(purchase: SteamPurchase) -> dict[str, Any]:
    row = build_investment_row(purchase)

    quantity = row.remaining_quantity
    total_spent = row.invested_active_rub
    current_value_after_fee = calculate_steam_net_amount(row.current_value_rub)
    profit_after_fee = quantize_money(current_value_after_fee - total_spent)

    hold_status_label = "готовы к продаже" if row.status_code == STATUS_READY else "в холде"
    trade_lock_label = "Предметы сняты с трейд-бана" if purchase.is_unlocked else "Предмет находится в трейд-бане"

    item_name = (
        purchase.item.name_ru
        or purchase.item.group_name
        or purchase.item.name
        or purchase.item.market_hash_name
    )

    return {
        "purchase_id": purchase.id,
        "item_id": purchase.item_id,
        "item_name": item_name,
        "item_url": build_item_absolute_url(purchase),
        "purchased_at": format_datetime_msk(purchase.purchased_at),
        "unlock_at": format_datetime_msk(purchase.unlocked_at),
        "quantity": quantity,
        "unit_buy_price": format_money(purchase.purchase_price_rub),
        "total_spent": format_money(total_spent),
        "current_price": format_money(row.current_price_rub),
        "current_value": format_money(row.current_value_rub),
        "current_value_after_fee": format_money(current_value_after_fee),
        "profit_abs": format_signed_money(row.unrealized_pnl_rub),
        "profit_pct": format_signed_percent(row.unrealized_pnl_percent),
        "profit_after_fee": format_signed_money(profit_after_fee),
        "hold_status_label": hold_status_label,
        "trade_lock_label": trade_lock_label,
    }


def render_investment_message(*, purchase: SteamPurchase, template_text: str | None = None) -> str:
    config = get_telegram_notifier_config()
    template_source = (template_text or config.investment_message_template or DEFAULT_INVESTMENT_MESSAGE_TEMPLATE).strip()

    engine = Engine(debug=False, string_if_invalid="—")
    template = engine.from_string(template_source)
    context = Context(build_investment_message_context(purchase), autoescape=False)
    rendered = template.render(context).strip()

    if not rendered:
        raise TelegramNotifierError("Шаблон инвестиционного сообщения отрендерился в пустой текст.")

    return rendered


def resolve_profile_chat_id(*, profile: UserProfile, allow_default: bool = False) -> str:
    chat_id = (profile.telegram_chat_id or "").strip()
    if chat_id:
        return chat_id

    if allow_default:
        default_chat_id = resolve_default_chat_id()
        if default_chat_id:
            return default_chat_id

    raise TelegramNotifierError("Telegram не подключен у пользователя.")


def send_user_message(*, user, text: str, allow_default: bool = False) -> TelegramSendResult:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    chat_id = resolve_profile_chat_id(profile=profile, allow_default=allow_default)
    return send_message(text=text, chat_id=chat_id)


def send_investment_purchase_notification(
    *,
    purchase: SteamPurchase,
    recipient_user=None,
    allow_default_fallback: bool = False,
) -> TelegramSendResult:
    text = render_investment_message(purchase=purchase)
    target_user = recipient_user or purchase.created_by
    return send_user_message(
        user=target_user,
        text=text,
        allow_default=allow_default_fallback,
    )


def build_test_message() -> str:
    timestamp = timezone.localtime().strftime("%d.%m.%Y %H:%M:%S")
    return f"✅ Test TG message from CaseUP\n\nВремя: {timestamp} МСК"


def get_active_link_token_for_user(user) -> TelegramLinkToken | None:
    now = timezone.now()
    return (
        TelegramLinkToken.objects
        .filter(user=user, used_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
        .first()
    )


def issue_link_token_for_user(user) -> TelegramLinkToken:
    now = timezone.now()
    existing = get_active_link_token_for_user(user)
    if existing is not None:
        return existing

    TelegramLinkToken.objects.filter(user=user, used_at__isnull=True, expires_at__lte=now).delete()
    return TelegramLinkToken.objects.create(user=user)


def build_link_start_param(link_token: TelegramLinkToken) -> str:
    return f"caseup_{link_token.token}"


def build_connect_url_for_user(user) -> str:
    config = get_telegram_notifier_config()
    bot_username = (config.bot_username or "").strip().lstrip("@")
    if not bot_username:
        raise TelegramNotifierError("Bot username не настроен.")

    link_token = issue_link_token_for_user(user)
    return f"https://t.me/{bot_username}?start={build_link_start_param(link_token)}"


def get_active_connect_url_for_user(user) -> str | None:
    config = get_telegram_notifier_config()
    bot_username = (config.bot_username or "").strip().lstrip("@")
    if not bot_username:
        return None

    link_token = get_active_link_token_for_user(user)
    if link_token is None:
        return None

    return f"https://t.me/{bot_username}?start={build_link_start_param(link_token)}"


def unlink_user_telegram(user) -> None:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.telegram_chat_id = None
    profile.telegram_username = ""
    profile.telegram_first_name = ""
    profile.telegram_linked_at = None
    profile.telegram_last_interaction_at = None
    profile.save(
        update_fields=[
            "telegram_chat_id",
            "telegram_username",
            "telegram_first_name",
            "telegram_linked_at",
            "telegram_last_interaction_at",
            "updated_at",
        ]
    )


def extract_start_token_from_message_text(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw.startswith("/start"):
        return None

    parts = raw.split(maxsplit=1)
    if len(parts) != 2:
        return None

    start_param = parts[1].strip()
    prefix = "caseup_"
    if not start_param.startswith(prefix):
        return None

    token = start_param[len(prefix):].strip()
    if not token:
        return None

    return token


@transaction.atomic
def bind_profile_by_telegram_start(
    *,
    start_token: str,
    chat_id: str,
    telegram_username: str = "",
    telegram_first_name: str = "",
) -> TelegramWebhookBindResult:
    link_token = (
        TelegramLinkToken.objects
        .select_for_update()
        .select_related("user")
        .filter(token=start_token)
        .first()
    )
    if link_token is None:
        raise TelegramNotifierError("Link token не найден.")

    if not link_token.is_active:
        raise TelegramNotifierError("Link token истек или уже использован.")

    profile, _ = UserProfile.objects.select_for_update().get_or_create(user=link_token.user)

    UserProfile.objects.select_for_update().filter(
        telegram_chat_id=chat_id
    ).exclude(pk=profile.pk).update(
        telegram_chat_id=None,
        telegram_username="",
        telegram_first_name="",
        telegram_linked_at=None,
        telegram_last_interaction_at=None,
    )

    now = timezone.now()
    profile.telegram_chat_id = chat_id
    profile.telegram_username = (telegram_username or "").strip()
    profile.telegram_first_name = (telegram_first_name or "").strip()
    profile.telegram_linked_at = now
    profile.telegram_last_interaction_at = now
    profile.save(
        update_fields=[
            "telegram_chat_id",
            "telegram_username",
            "telegram_first_name",
            "telegram_linked_at",
            "telegram_last_interaction_at",
            "updated_at",
        ]
    )

    link_token.used_at = now
    link_token.save(update_fields=["used_at"])

    return TelegramWebhookBindResult(
        profile_id=profile.id,
        chat_id=chat_id,
        telegram_username=profile.telegram_username,
        telegram_first_name=profile.telegram_first_name,
    )


def touch_profile_last_interaction_by_chat_id(chat_id: str) -> None:
    now = timezone.now()
    UserProfile.objects.filter(telegram_chat_id=chat_id).update(
        telegram_last_interaction_at=now,
    )


def build_webhook_url(config: TelegramNotifierConfig | None = None) -> str:
    """Where Telegram delivers bot updates: the site itself, or the relay that forwards them to the site."""
    config = config or get_telegram_notifier_config()
    relay_url, _relay_token = relay_settings()
    if relay_url:
        separator = "&" if "?" in relay_url else "?"
        return f"{relay_url}{separator}inbound={config.webhook_secret}"
    return f"{ensure_public_base_url(config)}{reverse('tg_notifier_public:webhook', args=[config.webhook_secret])}"

def register_webhook(config: TelegramNotifierConfig | None = None, timeout: int = 15) -> dict[str, Any]:
    config = config or get_telegram_notifier_config()

    bot_token = resolve_bot_token(config)
    if not bot_token:
        raise TelegramNotifierError("Bot token не настроен.")

    webhook_url = build_webhook_url(config)
    if not webhook_url.startswith("https://"):
        raise TelegramNotifierError("Telegram принимает webhook только по HTTPS URL.")

    payload = {
        "url": webhook_url,
        "drop_pending_updates": False,
        "secret_token": config.webhook_secret,
    }
    return _telegram_api_request(
        endpoint=_build_telegram_endpoint(bot_token, "setWebhook"),
        payload=payload,
        timeout=timeout,
    )


def get_webhook_info(config: TelegramNotifierConfig | None = None, timeout: int = 15) -> TelegramWebhookInfoResult:
    config = config or get_telegram_notifier_config()

    bot_token = resolve_bot_token(config)
    if not bot_token:
        raise TelegramNotifierError("Bot token не настроен.")

    data = _telegram_api_request(
        endpoint=_build_telegram_endpoint(bot_token, "getWebhookInfo"),
        payload={},
        timeout=timeout,
    )
    result = data.get("result") or {}

    return TelegramWebhookInfoResult(
        url=str(result.get("url") or ""),
        pending_update_count=int(result.get("pending_update_count") or 0),
        last_error_date=result.get("last_error_date"),
        last_error_message=str(result.get("last_error_message") or ""),
        max_connections=result.get("max_connections"),
        ip_address=str(result.get("ip_address") or ""),
        has_custom_certificate=bool(result.get("has_custom_certificate")),
    )
