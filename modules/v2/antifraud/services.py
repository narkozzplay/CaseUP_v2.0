"""Multi-account detection and IP bans.

* Every sign-in and session activity records the public IP of the account (UserIp).
* Hourly (antifraud.detect_multi_accounts) accounts that used the same IP within the last
  MULTI_ACCOUNT_WINDOW_DAYS are joined into groups (union–find). A new group, or a new account in a
  group already checked, opens a case and notifies admin+.
* An IP used by more than MULTI_ACCOUNT_SHARED_IP_LIMIT accounts is treated as a shared network
  (mobile operator / office NAT) and does not link accounts — it is listed separately.
* The admin decides: CLEAN (fine, e.g. family or own accounts) or ABUSE (ban the accounts and,
  optionally, their IPs for a while).
"""
from __future__ import annotations

import ipaddress
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.utils import timezone

from .models import IpBan, MultiAccountCase, MultiAccountMember, UserIp, normalize_network

logger = logging.getLogger(__name__)

IP_BANS_CACHE_KEY = "antifraud:active-ip-bans"
IP_BANS_CACHE_SECONDS = 60


def window_days() -> int:
    return int(getattr(settings, "MULTI_ACCOUNT_WINDOW_DAYS", 30))


def shared_ip_limit() -> int:
    return int(getattr(settings, "MULTI_ACCOUNT_SHARED_IP_LIMIT", 8))


def is_public_ip(ip: str | None) -> bool:
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_reserved)


# --------------------------------------------------------------------------- recording


def record_ip(user_id: int | None, ip: str | None, *, now=None) -> None:
    """Remember that the account used this IP. Cheap: one UPDATE, an INSERT only for a new IP."""
    if not user_id or not is_public_ip(ip):
        return
    now = now or timezone.now()
    try:
        updated = UserIp.objects.filter(user_id=user_id, ip=ip).update(last_seen=now, hits=F("hits") + 1)
        if not updated:
            with transaction.atomic():
                UserIp.objects.create(user_id=user_id, ip=ip, first_seen=now, last_seen=now)
    except IntegrityError:
        UserIp.objects.filter(user_id=user_id, ip=ip).update(last_seen=now)
    except Exception:  # noqa: BLE001 - bookkeeping must never break a request
        logger.exception("Could not record IP for user %s", user_id)


# --------------------------------------------------------------------------- detection


@dataclass
class DetectionResult:
    groups: int = 0
    new_cases: int = 0
    reopened: int = 0
    shared_networks: int = 0


def _groups(*, now) -> tuple[list[tuple[set[int], set[str]]], list[tuple[str, int]]]:
    since = now - timedelta(days=window_days())
    rows = list(UserIp.objects.filter(last_seen__gte=since).values_list("ip", "user_id"))
    by_ip: dict[str, set[int]] = defaultdict(set)
    for ip, user_id in rows:
        by_ip[ip].add(user_id)

    shared = sorted(((ip, len(users)) for ip, users in by_ip.items() if len(users) > shared_ip_limit()), key=lambda row: -row[1])
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    linking = {ip: users for ip, users in by_ip.items() if 2 <= len(users) <= shared_ip_limit()}
    for users in linking.values():
        first, *rest = sorted(users)
        for other in rest:
            parent[find(other)] = find(first)

    members: dict[int, set[int]] = defaultdict(set)
    ips: dict[int, set[str]] = defaultdict(set)
    for ip, users in linking.items():
        root = find(next(iter(users)))
        members[root].update(users)
        ips[root].add(ip)
    return [(members[root], ips[root]) for root in members], shared


@transaction.atomic
def detect(*, now=None) -> DetectionResult:
    now = now or timezone.now()
    groups, shared = _groups(now=now)
    result = DetectionResult(groups=len(groups), shared_networks=len(shared))
    cache.set("antifraud:shared-networks", shared[:50], 2 * 60 * 60)

    for user_ids, ips in groups:
        case_ids = list(
            MultiAccountMember.objects.filter(user_id__in=user_ids).values_list("case_id", flat=True).distinct().order_by("case_id")
        )
        if not case_ids:
            case = MultiAccountCase.objects.create(shared_ips=sorted(ips), created_at=now, updated_at=now, last_alert_at=now)
            MultiAccountMember.objects.bulk_create([MultiAccountMember(case=case, user_id=uid, added_at=now, is_new=False) for uid in sorted(user_ids)])
            result.new_cases += 1
            _alert(case, new_member_ids=sorted(user_ids), reason="new")
            continue

        case = MultiAccountCase.objects.select_for_update().get(pk=case_ids[0])
        for other in MultiAccountCase.objects.filter(pk__in=case_ids[1:]):  # two known groups turned out to be one
            for membership in other.memberships.all():
                MultiAccountMember.objects.get_or_create(case=case, user_id=membership.user_id, defaults={"added_at": membership.added_at, "is_new": membership.is_new})
            case.shared_ips = sorted(set(case.shared_ips or []) | set(other.shared_ips or []))
            IpBan.objects.filter(case=other).update(case=case)
            other.delete()

        known = set(case.memberships.values_list("user_id", flat=True))
        new_ids = sorted(user_ids - known)
        case.shared_ips = sorted(set(case.shared_ips or []) | ips)
        case.updated_at = now
        if new_ids:
            MultiAccountMember.objects.bulk_create([MultiAccountMember(case=case, user_id=uid, added_at=now, is_new=True) for uid in new_ids])
            if case.status != MultiAccountCase.Status.OPEN:
                case.status = MultiAccountCase.Status.OPEN
                result.reopened += 1
            case.last_alert_at = now
            _alert(case, new_member_ids=new_ids, reason="joined")
        case.save()
    return result


def _alert(case: MultiAccountCase, *, new_member_ids: list[int], reason: str) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_MULTI_ACCOUNT

    User = get_user_model()
    names = list(User.objects.filter(pk__in=new_member_ids).values_list("username", flat=True)[:5])
    payload = {"case": case.pk, "reason": reason, "accounts": names, "count": len(new_member_ids), "ips": (case.shared_ips or [])[:3]}
    for admin in admin_recipients():
        try:
            notify(admin, KIND_MULTI_ACCOUNT, object_id=case.pk, dedupe=False, payload=payload)
        except Exception:  # noqa: BLE001
            logger.exception("Multi-account notification failed for admin %s", admin.pk)


def admin_recipients():
    from django.db.models import Q

    from modules.v2.profiles.models import UserRole

    roles = (UserRole.ADMIN, UserRole.CHIEF_ADMIN, UserRole.SUPER_ADMIN)
    return list(get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(profile_v2__role__in=roles)).distinct())


# --------------------------------------------------------------------------- decisions


class DecisionError(ValueError):
    pass


def mark_clean(*, actor, case: MultiAccountCase, note: str = "", request=None) -> MultiAccountCase:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    case.status = MultiAccountCase.Status.CLEAN
    case.decided_by, case.decided_at = actor, timezone.now()
    case.note = _append_note(case.note, actor, f"Чисто. {note}".strip())
    case.save(update_fields=["status", "decided_by", "decided_at", "note"])
    case.memberships.update(is_new=False)
    log_admin_action(actor=actor, action=AuditAction.MULTI_ACCOUNT_DECISION, summary=f"Группа #{case.pk}: чисто", payload={"case": case.pk, "decision": "clean", "note": note}, request=request)
    return case


def mark_abuse(*, actor, case: MultiAccountCase, user_ids, duration: str, ban_ips: bool, ip_duration: str, note: str = "", request=None) -> dict:
    """Ban the chosen accounts (and optionally the shared IPs). Accounts the actor may not manage are skipped."""
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action
    from modules.v2.control_center.services.users import UserManagementError, ban_user
    from modules.v2.profiles.rbac import can_manage_user

    member_ids = set(case.memberships.values_list("user_id", flat=True))
    chosen = [int(uid) for uid in user_ids if str(uid).isdigit() and int(uid) in member_ids]
    if not chosen and not ban_ips:
        raise DecisionError("Выберите аккаунты для бана или бан IP.")
    reason = f"Мульти-аккаунт (группа #{case.pk})" + (f": {note}" if note else "")
    banned, skipped = [], []
    User = get_user_model()
    for user in User.objects.filter(pk__in=chosen):
        if not can_manage_user(actor, user):
            skipped.append(user.username)
            continue
        try:
            ban_user(actor=actor, target=user, reason=reason, duration=duration, request=request)
            banned.append(user.username)
        except UserManagementError as exc:
            logger.warning("Multi-account ban of %s skipped: %s", user.username, exc)
            skipped.append(user.username)

    ip_bans = []
    if ban_ips:
        for ip in case.shared_ips or []:
            ip_bans.append(create_ip_ban(actor=actor, network=ip, reason=reason, duration=ip_duration, case=case, request=request).network)

    _block_internal_referrals(case)
    case.status = MultiAccountCase.Status.ABUSE
    case.decided_by, case.decided_at = actor, timezone.now()
    case.note = _append_note(case.note, actor, f"Абуз: забанено {len(banned)}, IP {len(ip_bans)}. {note}".strip())
    case.save(update_fields=["status", "decided_by", "decided_at", "note"])
    case.memberships.update(is_new=False)
    log_admin_action(
        actor=actor, action=AuditAction.MULTI_ACCOUNT_DECISION, summary=f"Группа #{case.pk}: абуз, бан {len(banned)}",
        payload={"case": case.pk, "decision": "abuse", "banned": banned, "skipped": skipped, "ip_bans": ip_bans, "duration": duration}, request=request,
    )
    return {"banned": banned, "skipped": skipped, "ip_bans": ip_bans}


def _block_internal_referrals(case: MultiAccountCase) -> None:
    """Referrals inside one abusive group never count."""
    try:
        from modules.v2.referrals.models import Referral

        from modules.v2.referrals.welcome import deny_held

        ids = list(case.memberships.values_list("user_id", flat=True))
        internal = Referral.objects.filter(referrer_id__in=ids, referee_id__in=ids)
        referral_ids = list(internal.values_list("pk", flat=True))
        internal.update(review=Referral.Review.BLOCKED, flag_reason=f"Мульти-аккаунт (группа #{case.pk})", reviewed_at=timezone.now())
        deny_held(referral_ids, f"Мульти-аккаунт (группа #{case.pk})")
    except Exception:  # noqa: BLE001
        logger.exception("Could not block referrals of case %s", case.pk)


def _append_note(existing: str, actor, text: str) -> str:
    stamp = timezone.localtime().strftime("%d.%m.%Y %H:%M")
    line = f"[{stamp} {actor.username}] {text}"
    return f"{existing}\n{line}".strip()[-4000:]


# --------------------------------------------------------------------------- IP bans


def _duration(code: str):
    from modules.v2.control_center.services.users import BAN_DURATIONS

    if code not in BAN_DURATIONS:
        raise DecisionError("Неизвестная длительность.")
    return BAN_DURATIONS[code]


def create_ip_ban(*, actor, network: str, reason: str, duration: str, case=None, request=None) -> IpBan:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    network = normalize_network(network)
    reason = (reason or "").strip()
    if not reason:
        raise DecisionError("Укажите причину.")
    if request is not None:
        from modules.v2.control_center._compat import client_ip

        own = client_ip(request)
        if own and ipaddress.ip_address(own) in ipaddress.ip_network(network):
            raise DecisionError(f"Это ваш текущий IP ({own}) — бан заблокировал бы вам доступ.")
    delta = _duration(duration)
    ban = IpBan.objects.create(network=network, reason=reason[:255], created_by=actor, expires_at=timezone.now() + delta if delta else None, case=case)
    cache.delete(IP_BANS_CACHE_KEY)
    log_admin_action(actor=actor, action=AuditAction.IP_BAN, summary=f"{network}: {reason}"[:255], payload={"network": network, "duration": duration, "case": getattr(case, "pk", None)}, request=request)
    return ban


def lift_ip_ban(*, actor, ban: IpBan, request=None) -> None:
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    ban.lifted_at, ban.lifted_by = timezone.now(), actor
    ban.save(update_fields=["lifted_at", "lifted_by"])
    cache.delete(IP_BANS_CACHE_KEY)
    log_admin_action(actor=actor, action=AuditAction.IP_UNBAN, summary=ban.network, payload={"network": ban.network}, request=request)


def _active_networks():
    cached = cache.get(IP_BANS_CACHE_KEY)
    if cached is None:
        cached = list(IpBan.objects.active().values_list("network", flat=True))
        cache.set(IP_BANS_CACHE_KEY, cached, IP_BANS_CACHE_SECONDS)
    networks = []
    for raw in cached:
        try:
            networks.append(ipaddress.ip_network(raw))
        except ValueError:
            continue
    return networks


def is_ip_banned(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address.version == net.version and address in net for net in _active_networks())


# --------------------------------------------------------------------------- views helpers


def accounts_on_ip(ip: str, *, exclude_user_id: int | None = None) -> int:
    qs = UserIp.objects.filter(ip=ip)
    if exclude_user_id:
        qs = qs.exclude(user_id=exclude_user_id)
    return qs.count()


def user_ip_history(user, *, limit: int = 15) -> list[dict]:
    from modules.v2.accounts.geoip import lookup

    rows = list(UserIp.objects.filter(user=user).order_by("-last_seen")[:limit])
    others = dict(
        UserIp.objects.filter(ip__in=[row.ip for row in rows]).exclude(user=user).values("ip").annotate(n=Count("user_id")).values_list("ip", "n")
    )
    history = []
    for row in rows:
        city, country = lookup(row.ip)
        history.append({"ip": row.ip, "first_seen": row.first_seen, "last_seen": row.last_seen, "hits": row.hits, "others": others.get(row.ip, 0), "location": ", ".join(part for part in (city, country) if part)})
    return history


def prune(*, now=None) -> int:
    now = now or timezone.now()
    keep = int(getattr(settings, "USER_IP_RETENTION_DAYS", 180))
    deleted, _ = UserIp.objects.filter(last_seen__lt=now - timedelta(days=keep)).delete()
    return deleted
