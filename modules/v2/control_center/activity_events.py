"""Domain events for the activity journal (Control Center → Логи).

Receivers hook model saves and auth signals, so every place in the code that performs an action is
covered without touching it. Status changes are detected by remembering the tracked fields when an
instance is loaded (post_init). Every write goes through log_activity, which never raises.
"""
from __future__ import annotations

import functools
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import transaction
from django.db.models.signals import post_delete, post_init, post_save

from .models import ActivityCategory as C
from .services.activity import log_activity

logger = logging.getLogger(__name__)

_INITIAL = "_activity_initial"
_TRACKED: dict[str, tuple[str, ...]] = {}
_MISSING = object()


def _safe(receiver, *, savepoint: bool = True):
    """Receivers read related rows (instance.case.name...), so a failure there must not poison the
    surrounding transaction either: everything runs in a savepoint and errors are only logged."""

    @functools.wraps(receiver)
    def wrapper(*args, **kwargs):
        try:
            if savepoint:
                with transaction.atomic():
                    receiver(*args, **kwargs)
            else:
                receiver(*args, **kwargs)
        except Exception:  # noqa: BLE001 - the journal must never break the action it describes
            logger.exception("Activity receiver %s failed", receiver.__name__)

    return wrapper


def _attname(model, name: str) -> str:
    return model._meta.get_field(name).attname


def _remember(sender, instance, **kwargs):
    fields = _TRACKED.get(sender._meta.label)
    if not fields:
        return
    # Read straight from __dict__: deferred fields (.only()/.defer()) must not trigger extra queries.
    values = instance.__dict__
    setattr(instance, _INITIAL, {name: values.get(_attname(sender, name), _MISSING) for name in fields})


def _changed(instance, name: str) -> tuple[bool, object]:
    old = (getattr(instance, _INITIAL, None) or {}).get(name, _MISSING)
    new = instance.__dict__.get(_attname(type(instance), name), _MISSING)
    if old is _MISSING or new is _MISSING:
        return False, None
    return old != new, old


def _track(label: str, *fields: str) -> None:
    _TRACKED[label] = fields
    post_init.connect(_safe(_remember, savepoint=False), sender=label, weak=False, dispatch_uid=f"activity_init_{label}")


def _label(instance, name: str, value) -> str:
    """Human-readable choice label for a stored value (status codes are shown in Russian)."""
    choices = dict(type(instance)._meta.get_field(name).flatchoices)
    return str(choices.get(value, value if value is not None else "—"))


def _transition(instance, name: str, old) -> str:
    return f"{_label(instance, name, old)} → {_label(instance, name, getattr(instance, name))}"


def _money(value) -> str:
    return f"{value:.2f} ₽" if value is not None else "—"


def _remember_after_save(instance) -> None:
    _remember(type(instance), instance)


# --------------------------------------------------------------------------- auth & account


def _on_login(sender, request, user, **kwargs):
    log_activity(category=C.AUTH, event="auth.login", summary=f"Вход: {user.username}", user=user, actor=user, request=request)


def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_activity(category=C.AUTH, event="auth.logout", summary=f"Выход: {user.username}", user=user, actor=user, request=request)


def _on_login_failed(sender, credentials, request=None, **kwargs):
    # Django masks secrets in `credentials`; only the login name is kept.
    username = str((credentials or {}).get("username") or "")[:150]
    log_activity(category=C.SECURITY, event="auth.login_failed", summary=f"Неудачный вход: {username or '—'}", payload={"username": username}, request=request)


def _on_user_saved(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.ACCOUNT, event="account.registered", summary=f"Новый пользователь {instance.username}", user=instance, obj=instance)


def _on_consent(sender, instance, created, **kwargs):
    if created:
        log_activity(
            category=C.ACCOUNT,
            event="account.consent",
            summary=f"Принял документы (соглашение {instance.agreement_version}, политика {instance.privacy_version})",
            user=instance.user,
            actor=instance.user,
            obj=instance,
            payload={"agreement": instance.agreement_version, "privacy": instance.privacy_version, "pd_consent": instance.pd_consent_version, "cookies": instance.cookies_version},
        )


def _on_verification(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.ACCOUNT, event="account.verification_requested", summary=f"Заявка на верификацию: {instance.requested_level}", user=instance.user, obj=instance)
        return
    changed, old = _changed(instance, "status")
    if changed:
        log_activity(
            category=C.ACCOUNT,
            event="account.verification_reviewed",
            summary=f"Верификация {instance.requested_level}: {_transition(instance, 'status', old)}",
            user=instance.user,
            actor=instance.reviewed_by,
            obj=instance,
            payload={"from": old, "to": instance.status},
        )
    _remember_after_save(instance)


def _on_customization(sender, instance, created, **kwargs):
    if created:
        return  # created lazily on first view, not a user action
    changes = {}
    for name in _TRACKED.get(sender._meta.label, ()):
        changed, old = _changed(instance, name)
        if changed:
            new = instance.__dict__.get(_attname(sender, name))
            changes[name] = {"from": str(old) if old is not None else None, "to": str(new) if new is not None else None}
    if changes:
        log_activity(category=C.ACCOUNT, event="account.customization", summary=f"Изменил оформление: {', '.join(changes)}", user=instance.user, obj=instance, payload=changes)
    _remember_after_save(instance)


def _on_privacy(sender, instance, created, **kwargs):
    if created:
        return
    changes = {}
    for name in _TRACKED.get(sender._meta.label, ()):
        changed, old = _changed(instance, name)
        if changed:
            changes[name] = {"from": old, "to": instance.__dict__.get(name)}
    if changes:
        log_activity(category=C.ACCOUNT, event="account.privacy", summary=f"Изменил приватность: {', '.join(changes)}", user=instance.user, obj=instance, payload=changes)
    _remember_after_save(instance)


# --------------------------------------------------------------------------- money


def _on_ledger(sender, instance, created, **kwargs):
    if not created:
        return
    comment = f" · {instance.comment}" if instance.comment else ""
    log_activity(
        category=C.BALANCE,
        event=f"balance.{instance.reason}",
        summary=f"{instance.delta:+.2f} ₽, {'рыночный ' if instance.wallet == 'market' else ''}баланс {instance.balance_after:.2f} ₽{comment}",
        user=instance.user,
        actor=instance.created_by,
        amount=instance.delta,
        obj=instance,
        payload={"reason": instance.reason, "wallet": instance.wallet, "balance_after": f"{instance.balance_after:.2f}", "idempotency_key": instance.idempotency_key or ""},
    )


def _on_payment(sender, instance, created, **kwargs):
    if created:
        log_activity(
            category=C.PAYMENT,
            event="payment.created",
            summary=f"Платёж {_money(instance.amount)} через {instance.provider}{' (тест)' if instance.is_test else ''}",
            user=instance.user,
            amount=instance.amount,
            obj=instance,
            payload={"public_id": str(instance.public_id), "provider": instance.provider},
        )
    else:
        changed, old = _changed(instance, "status")
        if changed and instance.status in ("succeeded", "canceled", "failed"):
            log_activity(
                category=C.PAYMENT,
                event=f"payment.{instance.status}",
                summary=f"Платёж {_money(instance.amount)}: {_transition(instance, 'status', old)}{' · ' + instance.error_message if instance.error_message else ''}",
                user=instance.user,
                amount=instance.amount,
                obj=instance,
                payload={"public_id": str(instance.public_id), "external_id": instance.external_id, "from": old, "to": instance.status, "is_test": instance.is_test},
            )
    _remember_after_save(instance)


# --------------------------------------------------------------------------- cases


def _on_opening(sender, instance, created, **kwargs):
    if created:
        stattrak = "StatTrak™ " if instance.is_stattrak else ""
        log_activity(
            category=C.CASE,
            event="case.open",
            summary=f"Открыл «{instance.case.name}»: {stattrak}{instance.item.name} ({instance.exterior}), стоимость {_money(instance.item_value)}",
            user=instance.user,
            amount=-instance.price_paid if instance.price_paid else None,
            obj=instance,
            payload={"case": instance.case_id, "item": instance.item_id, "rarity": getattr(instance.item, "rarity", ""), "item_value": f"{instance.item_value:.2f}" if instance.item_value is not None else None, "float": str(instance.float_value) if instance.float_value is not None else None, "source": instance.source},
        )
    else:
        changed, old = _changed(instance, "sold_at")
        if changed and old is None and instance.sold_at is not None:
            log_activity(
                category=C.CASE,
                event="case.item_sold",
                summary=f"Продал {instance.item.name} за {_money(instance.sold_price)}",
                user=instance.user,
                amount=instance.sold_price,
                obj=instance,
            )
    _remember_after_save(instance)


def _on_terminal(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.CASE, event="case.terminal_started", summary=f"Терминал «{instance.case.name}»", user=instance.user, obj=instance)
    else:
        changed, old = _changed(instance, "status")
        if changed:
            event = "case.terminal_accepted" if instance.status == "accepted" else "case.terminal_declined"
            log_activity(category=C.CASE, event=event, summary=f"Терминал «{instance.case.name}»: {_transition(instance, 'status', old)}", user=instance.user, obj=instance)
    _remember_after_save(instance)


# --------------------------------------------------------------------------- store & subscriptions


def _on_store_purchase(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.STORE, event="store.purchase", summary=f"Купил «{instance.listing.title}» за {_money(instance.amount_rub)}", user=instance.user, amount=instance.amount_rub, obj=instance, payload={"category": instance.category, "product": instance.product_key})


def _on_bundle_purchase(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.STORE, event="store.bundle_purchase", summary=f"Купил набор «{instance.bundle_listing.title}» за {_money(instance.amount_rub)}", user=instance.user, amount=instance.amount_rub, obj=instance)


def _on_review(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.STORE, event="store.review", summary=f"Отзыв на «{instance.listing.title}»: {instance.rating}★", user=instance.user, obj=instance)


def _on_subscription(sender, instance, created, **kwargs):
    if created:
        log_activity(
            category=C.SUBSCRIPTION,
            event="subscription.assigned",
            summary=f"Подписка «{instance.plan.name}» ({instance.family}) до {instance.expires_at:%d.%m.%Y}" if instance.expires_at else f"Подписка «{instance.plan.name}» ({instance.family}) бессрочно",
            user=instance.user,
            obj=instance,
            payload={"plan": instance.plan_id, "status": instance.status},
        )
    else:
        changed, old = _changed(instance, "status")
        if changed:
            log_activity(category=C.SUBSCRIPTION, event="subscription.status", summary=f"Подписка «{instance.plan.name}»: {_transition(instance, 'status', old)}", user=instance.user, obj=instance, payload={"from": old, "to": instance.status})
    _remember_after_save(instance)


# --------------------------------------------------------------------------- trading & farm


def _on_investment_purchase(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.TRADING, event="trading.investment_purchase", summary=f"Закупка {instance.item} ×{instance.quantity} на {_money(instance.total_spent_rub)}", user=instance.created_by, amount=instance.total_spent_rub, obj=instance)


def _on_investment_sale(sender, instance, created, **kwargs):
    if created:
        log_activity(
            category=C.TRADING,
            event="trading.investment_sale",
            summary=f"Продажа ×{instance.quantity} на {_money(instance.total_sale_rub)}, прибыль {_money(instance.profit_rub)}",
            user=instance.created_by,
            amount=instance.total_sale_rub,
            obj=instance,
            payload={"purchase": instance.purchase_id, "profit": f"{instance.profit_rub:.2f}" if instance.profit_rub is not None else None},
        )


def _on_sale_order(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.TRADING, event="trading.order_created", summary=f"Заявка TradeHub #{instance.pk}: {instance.items_count} предм. на {_money(instance.total_payout_rub)}", user=instance.seller, amount=instance.total_payout_rub, obj=instance)
    else:
        changed, old = _changed(instance, "status")
        if changed:
            log_activity(category=C.TRADING, event="trading.order_status", summary=f"Заявка TradeHub #{instance.pk}: {_transition(instance, 'status', old)}", user=instance.seller, obj=instance, payload={"from": old, "to": instance.status})
    _remember_after_save(instance)


def _on_farm_account(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.FARM, event="farm.account_added", summary=f"Добавлен аккаунт фермы «{instance.title}» ({instance.steam_id64})", user=instance.owner, obj=instance)


def _on_farm_account_deleted(sender, instance, **kwargs):
    log_activity(category=C.FARM, event="farm.account_removed", summary=f"Удалён аккаунт фермы «{instance.title}» ({instance.steam_id64})", user=instance.owner, payload={"steam_id64": instance.steam_id64})


def _on_drop_session(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.FARM, event="farm.drop_session", summary=f"Сессия дропа «{instance.title}»", user=instance.owner, obj=instance)


def _on_drop_sale(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.FARM, event="farm.drop_sale", summary=f"Продажа дропа ×{instance.quantity} на {_money(instance.net_sale_rub)} ({instance.platform})", user=instance.owner, amount=instance.net_sale_rub, obj=instance)


# --------------------------------------------------------------------------- social


def _on_friend_request(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.SOCIAL, event="social.friend_request", summary=f"Заявка в друзья → {instance.to_user.username}", user=instance.from_user, obj=instance, payload={"to_user": instance.to_user_id})
    else:
        changed, old = _changed(instance, "status")
        if changed:
            log_activity(category=C.SOCIAL, event="social.friend_request_status", summary=f"Заявка от {instance.from_user.username}: {_transition(instance, 'status', old)}", user=instance.to_user, obj=instance, payload={"from_user": instance.from_user_id, "from": old, "to": instance.status})
    _remember_after_save(instance)


def _on_friendship_deleted(sender, instance, **kwargs):
    log_activity(category=C.SOCIAL, event="social.friend_removed", summary=f"Дружба удалена: {instance.user_a_id} ↔ {instance.user_b_id}", payload={"user_a": instance.user_a_id, "user_b": instance.user_b_id})


def _on_block(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.SOCIAL, event="social.user_blocked", summary=f"Заблокировал {instance.blocked.username}", user=instance.blocker, obj=instance, payload={"blocked": instance.blocked_id})


def _on_unblock(sender, instance, **kwargs):
    log_activity(category=C.SOCIAL, event="social.user_unblocked", summary=f"Разблокировал пользователя {instance.blocked_id}", user=instance.blocker, payload={"blocked": instance.blocked_id})


def _on_space(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.SOCIAL, event="social.space_created", summary=f"Создано: «{instance.title}» ({instance.space_type})", user=instance.owner, obj=instance)


def _on_membership(sender, instance, created, **kwargs):
    if created:
        log_activity(category=C.SOCIAL, event="social.space_joined", summary=f"Участник «{instance.space.title}» ({instance.role})", user=instance.user, obj=instance)


def _on_message(sender, instance, created, **kwargs):
    if created:
        # The text itself is private and stays out of the journal.
        log_activity(category=C.SOCIAL, event="social.message", summary=f"Сообщение в «{instance.space.title}»", user=instance.author, obj=instance, payload={"space": instance.space_id, "type": instance.message_type})


# --------------------------------------------------------------------------- moderation, admin


def _on_ban(sender, instance, created, **kwargs):
    if created:
        until = f" до {instance.expires_at:%d.%m.%Y %H:%M}" if instance.expires_at else " навсегда"
        log_activity(category=C.SECURITY, event="security.ban", summary=f"Бан {instance.user.username}{until}: {instance.reason}", user=instance.user, actor=instance.banned_by, obj=instance)
    else:
        changed, old = _changed(instance, "lifted_at")
        if changed and old is None and instance.lifted_at is not None:
            log_activity(category=C.SECURITY, event="security.unban", summary=f"Бан снят: {instance.user.username}", user=instance.user, actor=instance.lifted_by, obj=instance)
    _remember_after_save(instance)


def _on_admin_audit(sender, instance, created, **kwargs):
    if created:
        log_activity(
            category=C.ADMIN,
            event=f"admin.{instance.action}",
            summary=instance.summary or instance.get_action_display(),
            user=instance.target_user or instance.actor,
            actor=instance.actor,
            obj=instance,
            payload=instance.payload or {},
        )


# --------------------------------------------------------------------------- registration


def connect_activity_receivers() -> None:
    user_logged_in.connect(_safe(_on_login), weak=False, dispatch_uid="activity_login")
    user_logged_out.connect(_safe(_on_logout), weak=False, dispatch_uid="activity_logout")
    user_login_failed.connect(_safe(_on_login_failed), weak=False, dispatch_uid="activity_login_failed")
    post_save.connect(_safe(_on_user_saved), sender=get_user_model(), weak=False, dispatch_uid="activity_user_saved")

    _track("profiles.VerificationRequest", "status")
    _track("profile_customization.UserCustomization", "selected_background", "selected_avatar", "avatar_frame", "name_effect", "hero_effect")
    _track("social_friends.UserPrivacySettings", "who_can_send_friend_request", "who_can_send_direct", "who_can_see_online", "who_can_see_profile")
    _track("payments.Payment", "status")
    _track("case_simulator.SimOpening", "sold_at")
    _track("case_simulator.TerminalSession", "status")
    _track("subscriptions.UserSubscription", "status")
    _track("tradehub.SaleOrder", "status")
    _track("social_friends.FriendRequest", "status")
    _track("control_center.UserBan", "lifted_at")

    saves = {
        "accounts_v2.LegalConsent": _on_consent,
        "profiles.VerificationRequest": _on_verification,
        "profile_customization.UserCustomization": _on_customization,
        "social_friends.UserPrivacySettings": _on_privacy,
        "profiles.LedgerEntry": _on_ledger,
        "payments.Payment": _on_payment,
        "case_simulator.SimOpening": _on_opening,
        "case_simulator.TerminalSession": _on_terminal,
        "store.UserStorePurchase": _on_store_purchase,
        "store.UserBundlePurchase": _on_bundle_purchase,
        "store.StoreProductReview": _on_review,
        "subscriptions.UserSubscription": _on_subscription,
        "steam_trading.SteamPurchase": _on_investment_purchase,
        "steam_trading.SteamPurchaseSale": _on_investment_sale,
        "tradehub.SaleOrder": _on_sale_order,
        "farm.FarmAccount": _on_farm_account,
        "farm.FarmDropSession": _on_drop_session,
        "farm.FarmDropSale": _on_drop_sale,
        "social_friends.FriendRequest": _on_friend_request,
        "social_friends.UserBlock": _on_block,
        "social_layer.SocialSpace": _on_space,
        "social_layer.SocialMembership": _on_membership,
        "social_layer.SocialMessage": _on_message,
        "control_center.UserBan": _on_ban,
        "control_center.AdminAuditLog": _on_admin_audit,
    }
    for label, receiver in saves.items():
        post_save.connect(_safe(receiver), sender=label, weak=False, dispatch_uid=f"activity_save_{label}")

    deletes = {
        "farm.FarmAccount": _on_farm_account_deleted,
        "social_friends.Friendship": _on_friendship_deleted,
        "social_friends.UserBlock": _on_unblock,
    }
    for label, receiver in deletes.items():
        post_delete.connect(_safe(receiver), sender=label, weak=False, dispatch_uid=f"activity_delete_{label}")
