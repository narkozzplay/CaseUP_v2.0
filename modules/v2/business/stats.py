"""Business numbers for the Control Center «Бизнес» tab and the daily e-mail report.

Money terms used everywhere:
- top-ups: real money paid through the payment provider (test payments excluded);
- revenue: platform income from the revenue journal (commissions, subscriptions, store, cases);
- bonus costs: balance given away (referral and promo bonuses, net admin adjustments);
- net income: revenue minus bonus costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Min, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile

from .models import RevenueCategory, RevenueEntry, RevenueSource

User = get_user_model()
ZERO = Decimal("0.00")
CACHE_SECONDS = 300
DAILY_BUCKETS_MAX = 92
TOP_LIMIT = 10

PERIOD_PRESETS = (
    ("today", "Сегодня"),
    ("7d", "7 дней"),
    ("30d", "30 дней"),
    ("90d", "90 дней"),
    ("all", "Всё время"),
)
PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90}
CATEGORY_LABELS = dict(RevenueCategory.choices)
CATEGORY_ORDER = (RevenueCategory.COMMISSION, RevenueCategory.SUBSCRIPTION, RevenueCategory.STORE, RevenueCategory.CASES)
COMMISSION_SOURCES = (RevenueSource.SWITCH_FEE, RevenueSource.TRADEHUB, RevenueSource.MARKET)
BONUS_REASONS = {
    LedgerReason.REFERRAL_BONUS: "Реферальные бонусы",
    LedgerReason.PROMO_BONUS: "Бонусы промокодов",
    LedgerReason.DAILY_BONUS: "Ежедневные бонусы",
    LedgerReason.ADMIN_ADJUST: "Начисления админами (нетто)",
}


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: datetime | None  # None: since the very beginning
    end: datetime
    prev_start: datetime | None
    prev_end: datetime | None
    date_from: date | None
    date_to: date

    @property
    def cache_key(self) -> str:
        start = self.start.isoformat() if self.start else "all"
        # The open end moves every request: round it to the cache window so the cache can hit.
        end = int(self.end.timestamp()) // CACHE_SECONDS
        return f"business:stats:{start}:{end}"


def _midnight(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def resolve_period(key: str = "", date_from: date | None = None, date_to: date | None = None, *, now: datetime | None = None) -> Period:
    now = now or timezone.now()
    today = timezone.localdate(now)
    if date_from or date_to:
        date_from = date_from or date_to
        date_to = date_to or today
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        start, end = _midnight(date_from), min(_midnight(date_to + timedelta(days=1)), now)
        length = end - start
        label = f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}"
        return Period("custom", label, start, end, start - length, start, date_from, date_to)
    if key == "all":
        return Period("all", "Всё время", None, now, None, None, None, today)
    if key == "today":
        start = _midnight(today)
        return Period("today", "Сегодня", start, now, start - timedelta(days=1), now - timedelta(days=1), today, today)
    key = key if key in PRESET_DAYS else "30d"
    days = PRESET_DAYS[key]
    start = _midnight(today - timedelta(days=days - 1))
    return Period(key, dict(PERIOD_PRESETS)[key], start, now, start - timedelta(days=days), start, today - timedelta(days=days - 1), today)


def day_period(day: date) -> Period:
    """One whole calendar day (the daily report), compared with the day before."""
    start, end = _midnight(day), _midnight(day + timedelta(days=1))
    return Period("custom", f"{day:%d.%m.%Y}", start, end, start - timedelta(days=1), start, day, day)


def _range(field: str, start, end) -> Q:
    q = Q(**{f"{field}__lt": end})
    if start is not None:
        q &= Q(**{f"{field}__gte": start})
    return q


def change_percent(current, previous) -> int | None:
    current, previous = Decimal(current or 0), Decimal(previous or 0)
    if previous == 0:
        return None
    return int(round((current - previous) * 100 / abs(previous)))


def _paid_payments():
    from modules.v2.payments.models import Payment

    return Payment.objects.filter(status=Payment.Status.SUCCEEDED, is_test=False)


# --------------------------------------------------------------------------- money


def money_summary(start, end) -> dict:
    payments = _paid_payments().filter(_range("paid_at", start, end))
    topups = payments.aggregate(total=Sum("amount"), count=Count("id"), payers=Count("user_id", distinct=True))

    revenue_qs = RevenueEntry.objects.filter(_range("occurred_at", start, end))
    by_category = {row["category"]: row["total"] for row in revenue_qs.values("category").annotate(total=Sum("amount"))}
    by_source = {
        row["source"]: {"total": row["total"], "count": row["count"]}
        for row in revenue_qs.values("source").annotate(total=Sum("amount"), count=Count("id"))
    }
    revenue_total = sum(by_category.values(), ZERO)

    bonus_rows = {
        row["reason"]: row["total"]
        for row in LedgerEntry.objects.filter(_range("created_at", start, end), reason__in=list(BONUS_REASONS))
        .values("reason")
        .annotate(total=Sum("delta"))
    }
    bonuses = [{"reason": reason, "label": label, "total": bonus_rows.get(reason) or ZERO} for reason, label in BONUS_REASONS.items()]
    bonus_total = sum((row["total"] for row in bonuses), ZERO)

    return {
        "topups": topups["total"] or ZERO,
        "topups_count": topups["count"] or 0,
        "payers": topups["payers"] or 0,
        "revenue": revenue_total,
        "categories": {code: by_category.get(code) or ZERO for code in CATEGORY_ORDER},
        "sources": by_source,
        "commission": by_category.get(RevenueCategory.COMMISSION) or ZERO,
        "bonuses": bonuses,
        "bonus_total": bonus_total,
        "net": revenue_total - bonus_total,
        "registrations": User.objects.filter(_range("date_joined", start, end)).count(),
    }


def _bucket_start(period: Period) -> datetime | None:
    if period.start is not None:
        return period.start
    first = RevenueEntry.objects.aggregate(first=Min("occurred_at"))["first"]
    first_payment = _paid_payments().aggregate(first=Min("paid_at"))["first"]
    candidates = [moment for moment in (first, first_payment) if moment]
    return _midnight(timezone.localdate(min(candidates))) if candidates else None


def revenue_series(period: Period) -> dict:
    """Per-day (or per-month for long periods) top-ups and revenue by category."""
    start = _bucket_start(period)
    if start is None:
        return {"monthly": False, "rows": []}
    first_day, last_day = timezone.localdate(start), timezone.localdate(period.end - timedelta(microseconds=1))
    monthly = (last_day - first_day).days + 1 > DAILY_BUCKETS_MAX
    trunc = TruncMonth if monthly else TruncDate

    def bucket(value) -> date:
        value = value.date() if isinstance(value, datetime) else value
        return value.replace(day=1) if monthly else value

    if monthly:
        keys, cursor = [], first_day.replace(day=1)
        while cursor <= last_day:
            keys.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)
    else:
        keys = [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]

    topups = {
        bucket(row["bucket"]): row["total"]
        for row in _paid_payments().filter(_range("paid_at", start, period.end)).annotate(bucket=trunc("paid_at")).values("bucket").annotate(total=Sum("amount"))
    }
    revenue: dict[date, dict[str, Decimal]] = {}
    for row in (
        RevenueEntry.objects.filter(_range("occurred_at", start, period.end))
        .annotate(bucket=trunc("occurred_at"))
        .values("bucket", "category")
        .annotate(total=Sum("amount"))
    ):
        revenue.setdefault(bucket(row["bucket"]), {})[row["category"]] = row["total"]

    rows = []
    for key in keys:
        parts = revenue.get(key, {})
        rows.append(
            {
                "date": key,
                "topups": topups.get(key) or ZERO,
                "revenue": sum(parts.values(), ZERO),
                "parts": [{"code": code, "label": CATEGORY_LABELS[code], "value": parts.get(code) or ZERO} for code in CATEGORY_ORDER],
            }
        )
    topup_peak = max((row["topups"] for row in rows), default=ZERO)
    stack_peak = max((sum((max(p["value"], ZERO) for p in row["parts"]), ZERO) for row in rows), default=ZERO)
    label_every = max(1, -(-len(rows) // 7))  # about seven date labels, whatever the period
    for index, row in enumerate(rows):
        row["topup_percent"] = int(row["topups"] * 100 / topup_peak) if topup_peak else 0
        for part in row["parts"]:
            part["percent"] = int(max(part["value"], ZERO) * 100 / stack_peak) if stack_peak else 0
        row["show_label"] = index % label_every == 0 or index == len(rows) - 1
    return {"monthly": monthly, "rows": rows}


def commission_breakdown(summary: dict) -> list[dict]:
    total_revenue = summary["revenue"]
    total_commission = summary["commission"]
    labels = dict(RevenueSource.choices)
    rows = []
    for source in COMMISSION_SOURCES:
        data = summary["sources"].get(source) or {"total": ZERO, "count": 0}
        total = data["total"] or ZERO
        rows.append(
            {
                "source": source,
                "label": labels[source],
                "total": total,
                "count": data["count"],
                "average": (total / data["count"]).quantize(Decimal("0.01")) if data["count"] else ZERO,
                "share_commission": int(total * 100 / total_commission) if total_commission > 0 else 0,
                "share_revenue": int(total * 100 / total_revenue) if total_revenue > 0 else 0,
            }
        )
    return rows


# --------------------------------------------------------------------------- users


def users_summary(period: Period, money: dict) -> dict:
    start, end = period.start, period.end
    from modules.v2.payments.models import Payment

    paid = _paid_payments()
    cohort = User.objects.filter(_range("date_joined", start, end))
    paid_filter = Q(payments__status=Payment.Status.SUCCEEDED, payments__is_test=False)
    cohort_stats = cohort.annotate(paid_count=Count("payments", filter=paid_filter)).aggregate(
        registered=Count("id"),
        paid_once=Count("id", filter=Q(paid_count__gte=1)),
        paid_twice=Count("id", filter=Q(paid_count__gte=2)),
    )
    registered = cohort_stats["registered"] or 0

    period_payers = paid.filter(_range("paid_at", start, end)).values("user_id")
    returning = 0
    if start is not None:
        returning = paid.filter(paid_at__lt=start, user_id__in=period_payers).values("user_id").distinct().count()
    payers = money["payers"]
    users_total = User.objects.filter(date_joined__lt=end).count()
    return {
        "registered": registered,
        "paid_once": cohort_stats["paid_once"] or 0,
        "paid_twice": cohort_stats["paid_twice"] or 0,
        "conversion_first": int((cohort_stats["paid_once"] or 0) * 100 / registered) if registered else 0,
        "conversion_repeat": int((cohort_stats["paid_twice"] or 0) * 100 / registered) if registered else 0,
        "payers": payers,
        "new_payers": payers - returning,
        "returning_payers": returning,
        "avg_check": (money["topups"] / money["topups_count"]).quantize(Decimal("0.01")) if money["topups_count"] else ZERO,
        "arpu": (money["revenue"] / users_total).quantize(Decimal("0.01")) if users_total else ZERO,
        "arppu": (money["revenue"] / payers).quantize(Decimal("0.01")) if payers else ZERO,
        "users_total": users_total,
    }


def top_users(period: Period) -> dict:
    by_topups = list(
        _paid_payments().filter(_range("paid_at", period.start, period.end)).values("user_id").annotate(total=Sum("amount"), count=Count("id")).order_by("-total")[:TOP_LIMIT]
    )
    by_revenue = list(
        RevenueEntry.objects.filter(_range("occurred_at", period.start, period.end), user_id__isnull=False)
        .values("user_id")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:TOP_LIMIT]
    )
    return {"topups": by_topups, "revenue": [row for row in by_revenue if row["total"] > 0]}


# --------------------------------------------------------------------------- products


def subscriptions_summary(period: Period, *, now: datetime | None = None) -> dict:
    from modules.v2.subscriptions.models import UserSubscription

    now = now or timezone.now()
    live = UserSubscription.objects.filter(status=UserSubscription.Status.ACTIVE).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    plans = []
    mrr = ZERO
    for row in live.values("plan__name", "plan__family", "plan__price_rub", "plan__billing_period_days").annotate(count=Count("id")).order_by("plan__family", "plan__price_rub"):
        days = row["plan__billing_period_days"] or 30
        monthly = (row["plan__price_rub"] or ZERO) * 30 / days * row["count"]
        mrr += monthly
        plans.append({"name": row["plan__name"], "family": row["plan__family"], "count": row["count"], "price": row["plan__price_rub"] or ZERO, "mrr": monthly.quantize(Decimal("0.01"))})

    window_end = min(period.end, now)
    ended = UserSubscription.objects.filter(_range("expires_at", period.start, window_end)).exclude(status=UserSubscription.Status.CANCELED)
    ended_users = set(ended.values_list("user_id", "family"))
    still_active = set(live.filter(user_id__in={user for user, _ in ended_users}).values_list("user_id", "family"))
    churned = len(ended_users - still_active)
    return {
        "active": sum(row["count"] for row in plans),
        "plans": plans,
        "mrr": mrr.quantize(Decimal("0.01")),
        "new": UserSubscription.objects.filter(_range("created_at", period.start, period.end)).count(),
        "ended": len(ended_users),
        "renewed": len(ended_users) - churned,
        "churned": churned,
        "churn_percent": int(churned * 100 / len(ended_users)) if ended_users else 0,
    }


def cases_summary(period: Period) -> dict:
    from modules.v2.case_simulator.models import SimOpening

    rows = list(
        SimOpening.objects.filter(_range("created_at", period.start, period.end), source=SimOpening.Source.CASE)
        .values("case_id", "case__name")
        .annotate(openings=Count("id"), paid=Sum("price_paid"), given=Sum("item_value"), bought_back=Sum("sold_price"))
        .order_by("-paid", "-openings")
    )
    totals = {"openings": 0, "paid": ZERO, "given": ZERO, "bought_back": ZERO}
    for row in rows:
        for key in ("paid", "given", "bought_back"):
            row[key] = row[key] or ZERO
        row["margin"] = row["paid"] - row["bought_back"]
        row["rtp"] = int(row["given"] * 100 / row["paid"]) if row["paid"] else None
        for key in totals:
            totals[key] += row[key]
    totals["margin"] = totals["paid"] - totals["bought_back"]
    totals["rtp"] = int(totals["given"] * 100 / totals["paid"]) if totals["paid"] else None
    return {"rows": rows[:TOP_LIMIT], "totals": totals}


def store_summary(period: Period) -> list[dict]:
    from modules.v2.store.models import UserBundlePurchase, UserStorePurchase

    items: dict[str, dict] = {}
    sources = (
        (UserStorePurchase.objects.filter(_range("purchased_at", period.start, period.end), amount_rub__gt=0).values("listing__title", "category"), "listing__title"),
        (UserBundlePurchase.objects.filter(_range("purchased_at", period.start, period.end), amount_rub__gt=0).values("bundle_listing__title"), "bundle_listing__title"),
    )
    for qs, title_field in sources:
        for row in qs.annotate(count=Count("id"), total=Sum("amount_rub")):
            title = row[title_field] or "—"
            kind = "Набор" if title_field.startswith("bundle") else (row.get("category") or "")
            slot = items.setdefault(f"{kind}:{title}", {"title": title, "kind": kind, "count": 0, "total": ZERO})
            slot["count"] += row["count"]
            slot["total"] += row["total"] or ZERO
    return sorted(items.values(), key=lambda item: item["total"], reverse=True)[:TOP_LIMIT]


# --------------------------------------------------------------------------- marketing


def marketing_summary(period: Period) -> dict:
    from modules.v2.promo.models import PromoActivation
    from modules.v2.referrals.models import Referral

    start, end = period.start, period.end
    paid_in_period = _paid_payments().filter(_range("paid_at", start, end))
    referee_ids = Referral.objects.exclude(review=Referral.Review.BLOCKED).values("referee_id")
    referral_bonus = LedgerEntry.objects.filter(_range("created_at", start, end), reason=LedgerReason.REFERRAL_BONUS).aggregate(total=Sum("delta"))["total"] or ZERO
    referral_topups = paid_in_period.filter(user_id__in=referee_ids).aggregate(total=Sum("amount"))["total"] or ZERO
    referral = {
        "new": Referral.objects.filter(_range("created_at", start, end)).count(),
        "qualified": Referral.objects.filter(_range("qualified_at", start, end)).count(),
        "bonus": referral_bonus,
        "topups": referral_topups,
        "roi": (referral_topups / referral_bonus).quantize(Decimal("0.1")) if referral_bonus > 0 else None,
    }

    activations = PromoActivation.objects.filter(_range("created_at", start, end)).exclude(status=PromoActivation.Status.CANCELED)
    codes = list(
        activations.values("promo_id", "promo__code", "promo__title", "promo__kind")
        .annotate(count=Count("id"), benefit=Sum("benefit_amount"))
        .order_by("-count")[:TOP_LIMIT]
    )
    for row in codes:
        first_use = {
            user_id: activated_at
            for user_id, activated_at in PromoActivation.objects.filter(promo_id=row["promo_id"])
            .exclude(status=PromoActivation.Status.CANCELED)
            .values_list("user_id", "created_at")
        }
        topups = ZERO
        for user_id, amount, paid_at in paid_in_period.filter(user_id__in=list(first_use)).values_list("user_id", "amount", "paid_at"):
            if paid_at and paid_at >= first_use[user_id]:
                topups += amount
        row["benefit"] = row["benefit"] or ZERO
        row["topups"] = topups
    promo_bonus = LedgerEntry.objects.filter(_range("created_at", start, end), reason=LedgerReason.PROMO_BONUS).aggregate(total=Sum("delta"))["total"] or ZERO
    return {"referral": referral, "promo_codes": codes, "promo_bonus": promo_bonus, "promo_activations": activations.count()}


# --------------------------------------------------------------------------- assembly


def _compare(current: dict, previous: dict | None) -> dict:
    if previous is None:
        return {}
    keys = ("topups", "revenue", "commission", "bonus_total", "net", "registrations", "payers")
    result = {key: change_percent(current[key], previous[key]) for key in keys}
    result["categories"] = {code: change_percent(current["categories"][code], previous["categories"][code]) for code in CATEGORY_ORDER}
    return result


def build_dashboard(period: Period, *, use_cache: bool = True) -> dict:
    def build() -> dict:
        money = money_summary(period.start, period.end)
        previous = money_summary(period.prev_start, period.prev_end) if period.prev_end is not None else None
        return {
            "money": money,
            "previous": previous,
            "changes": _compare(money, previous),
            "series": revenue_series(period),
            "commissions": commission_breakdown(money),
            "users": users_summary(period, money),
            "top_users": top_users(period),
            "subscriptions": subscriptions_summary(period),
            "cases": cases_summary(period),
            "store": store_summary(period),
            "marketing": marketing_summary(period),
            "balances_total": UserProfile.objects.aggregate(total=Sum("balance_coins"))["total"] or ZERO,
            "built_at": timezone.now(),
        }

    if not use_cache:
        return build()
    return cache.get_or_set(period.cache_key, build, CACHE_SECONDS)


def revenue_journal(period: Period, *, category: str = "", source: str = ""):
    qs = RevenueEntry.objects.select_related("user", "user__profile_v2").filter(_range("occurred_at", period.start, period.end))
    if category in RevenueCategory.values:
        qs = qs.filter(category=category)
    if source in RevenueSource.values:
        qs = qs.filter(source=source)
    return qs.order_by("-occurred_at", "-id")
