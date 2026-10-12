from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from modules.v2.profiles.models import UserProfile
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from . import services
from .models import ZERO, SimCase, SimCaseItem, SimOpening, TerminalSession

MENU_KEY = "case_simulator"
FEED_RARITIES = (SimCaseItem.Rarity.CLASSIFIED, SimCaseItem.Rarity.COVERT, SimCaseItem.Rarity.GOLD)


def _balance(user) -> Decimal:
    value = UserProfile.objects.filter(user=user).values_list("balance_coins", flat=True).first()
    return Decimal(value or ZERO)


def _user_stats(user) -> dict:
    stats = SimOpening.objects.filter(user=user).aggregate(count=Count("id"), spent=Sum("price_paid"), value=Sum("item_value"))
    return {
        "count": stats["count"] or 0,
        "spent": stats["spent"] or ZERO,
        "value": stats["value"] or ZERO,
        "balance": _balance(user),
    }


def _with_previews(cases: list[SimCase]) -> list[SimCase]:
    """Attach the most valuable items so cards without a cover still look like the case."""
    for case in cases:
        items = sorted(case.items.all(), key=lambda item: item.value, reverse=True)
        case.preview_items = items[:3]
        case.item_count = len(items)
    return cases


def _recent_drops(limit: int = 14):
    return list(
        SimOpening.objects.filter(item__rarity__in=FEED_RARITIES)
        .select_related("item", "case", "user")
        .order_by("-created_at")[:limit]
    )


@login_required
def case_list(request):
    cases = _with_previews(list(SimCase.objects.filter(is_active=True).prefetch_related("items")))
    groups = [
        {"key": SimCase.Kind.CUSTOM, "title": "Кейсы CaseUP", "lead": "Наши коллекции. Открываются за баланс сайта, без ключа или с ключом.", "cases": [c for c in cases if c.kind == SimCase.Kind.CUSTOM]},
        {"key": SimCase.Kind.OFFICIAL, "title": "Кейсы CS2", "lead": "Настоящие кейсы из игры с теми же шансами: кейс + ключ.", "cases": [c for c in cases if c.kind == SimCase.Kind.OFFICIAL]},
        {"key": SimCase.Kind.TERMINAL, "title": "Терминал", "lead": "Пять предложений по очереди. Бери понравившееся или жди следующее.", "cases": [c for c in cases if c.kind == SimCase.Kind.TERMINAL]},
    ]
    selected = request.GET.get("kind") or ""
    if selected not in SimCase.Kind.values:
        selected = ""
    return render_dashboard_page(
        request,
        full_template="case_simulator/list.html",
        partial_template="case_simulator/partials/list_content.html",
        context={
            "groups": [g for g in groups if g["cases"] and (not selected or g["key"] == selected)],
            "group_tabs": [(g["key"], g["title"], len(g["cases"])) for g in groups if g["cases"]],
            "selected_kind": selected,
            "total_cases": len(cases),
            "stats": _user_stats(request.user),
            "recent_drops": _recent_drops(),
        },
        page_title=build_page_title("Кейсы"),
        active_menu_key=MENU_KEY,
    )


@login_required
def case_detail(request, slug: str):
    case = get_object_or_404(SimCase.objects.prefetch_related("items"), slug=slug, is_active=True)
    if case.is_terminal:
        return redirect("case_simulator:terminal", slug=case.slug)
    _with_previews([case])
    contents = services.drop_chances(case)
    strip = [services.item_payload(item) for item in services.build_strip(case, contents[0]["item"])] if contents else []
    my_drops = list(SimOpening.objects.filter(user=request.user, case=case).select_related("item")[:8])
    return render_dashboard_page(
        request,
        full_template="case_simulator/detail.html",
        partial_template="case_simulator/partials/detail_content.html",
        context={
            "case": case,
            "contents": contents,
            "rarity_summary": services.rarity_summary(case),
            "idle_strip": strip,
            "open_counts": range(1, services.MAX_OPEN_COUNT + 1),
            "stats": _user_stats(request.user),
            "my_drops": my_drops,
        },
        page_title=build_page_title(case.name, "Кейсы"),
        active_menu_key=MENU_KEY,
    )


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


@login_required
@require_POST
def case_open(request, slug: str):
    case = get_object_or_404(SimCase.objects.prefetch_related("items"), slug=slug, is_active=True)
    if case.is_terminal:
        return _error("Терминал открывается на своей странице.")
    try:
        count = int(request.POST.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, services.MAX_OPEN_COUNT))
    demo = request.POST.get("demo") == "1"

    try:
        if demo:
            results = [services.demo_payload(services.roll_case(case)) for _ in range(count)]
        else:
            results = [services.roll_payload(opening) for opening in services.open_case(user=request.user, case=case, count=count)]
    except services.SimulatorError as exc:
        return _error(str(exc), status=402 if "средств" in str(exc) else 400)

    winners = {item.pk: item for item in case.items.all()}
    strips = [
        [services.item_payload(item) for item in services.build_strip(case, winners[result["id"]])]
        for result in results
    ]
    return JsonResponse({
        "ok": True,
        "demo": demo,
        "results": results,
        "strips": strips,
        "win_index": services.STRIP_WIN_INDEX,
        "balance": str(_balance(request.user)),
        "total_value": str(sum((Decimal(r["value"]) for r in results), ZERO)),
        "spent": "0" if demo else str(case.open_price * count),
    })


def _terminal_state(session: TerminalSession | None, *, user) -> dict:
    state = {"session": None, "offer": None, "status": None, "balance": str(_balance(user))}
    if session is None:
        return state
    state.update({
        "session": session.pk,
        "status": session.status,
        "offers_total": len(session.offers),
        "offers_left": session.offers_left,
        "offer": services.offer_payload(session),
    })
    if session.opening_id:
        state["result"] = services.roll_payload(session.opening)
    return state


@login_required
def terminal_page(request, slug: str):
    case = get_object_or_404(SimCase.objects.prefetch_related("items"), slug=slug, is_active=True, kind=SimCase.Kind.TERMINAL)
    session = TerminalSession.objects.filter(user=request.user, case=case, status=TerminalSession.Status.ACTIVE).first()
    history = list(
        TerminalSession.objects.filter(user=request.user, case=case)
        .exclude(status=TerminalSession.Status.ACTIVE)
        .select_related("opening__item")[:6]
    )
    return render_dashboard_page(
        request,
        full_template="case_simulator/terminal.html",
        partial_template="case_simulator/partials/terminal_content.html",
        context={
            "case": case,
            "contents": services.drop_chances(case),
            "rarity_summary": services.rarity_summary(case),
            "terminal_state": _terminal_state(session, user=request.user),
            "stats": _user_stats(request.user),
            "history": history,
        },
        page_title=build_page_title(case.name, "Кейсы"),
        active_menu_key=MENU_KEY,
    )


@login_required
@require_POST
def terminal_start(request, slug: str):
    case = get_object_or_404(SimCase.objects.prefetch_related("items"), slug=slug, is_active=True, kind=SimCase.Kind.TERMINAL)
    try:
        session = services.start_terminal(user=request.user, case=case)
    except services.SimulatorError as exc:
        return _error(str(exc), status=402 if "средств" in str(exc) else 400)
    return JsonResponse({"ok": True, **_terminal_state(session, user=request.user)})


@login_required
@require_POST
def terminal_accept(request, session_id: int):
    try:
        opening = services.accept_terminal_offer(user=request.user, session_id=session_id)
    except services.SimulatorError as exc:
        return _error(str(exc), status=402 if "средств" in str(exc) else 400)
    session = TerminalSession.objects.select_related("opening__item").get(pk=opening.terminal_session.pk)
    return JsonResponse({"ok": True, **_terminal_state(session, user=request.user)})


@login_required
@require_POST
def terminal_decline(request, session_id: int):
    try:
        session = services.decline_terminal_offer(user=request.user, session_id=session_id)
    except services.SimulatorError as exc:
        return _error(str(exc))
    return JsonResponse({"ok": True, **_terminal_state(session, user=request.user)})


@login_required
def inventory(request):
    rarity = request.GET.get("rarity") or ""
    queryset = SimOpening.objects.filter(user=request.user).select_related("item", "case")
    rarity_counts = dict(queryset.values_list("item__rarity").annotate(n=Count("id")).values_list("item__rarity", "n"))
    if rarity in SimCaseItem.Rarity.values:
        queryset = queryset.filter(item__rarity=rarity)
    else:
        rarity = ""
    page_obj = Paginator(queryset, 36).get_page(request.GET.get("page") or 1)
    labels = dict(SimCaseItem.Rarity.choices)
    rarity_tabs = [(key, labels[key], rarity_counts[key]) for key in services.RARITY_ORDER if rarity_counts.get(key)]
    best = SimOpening.objects.filter(user=request.user).select_related("item").order_by("-item_value").first()
    return render_dashboard_page(
        request,
        full_template="case_simulator/inventory.html",
        partial_template="case_simulator/partials/inventory_content.html",
        context={
            "page_obj": page_obj,
            "openings": list(page_obj.object_list),
            "rarity_tabs": rarity_tabs,
            "selected_rarity": rarity,
            "stats": _user_stats(request.user),
            "best_drop": best,
        },
        page_title=build_page_title("Инвентарь симулятора", "Кейсы"),
        active_menu_key=MENU_KEY,
    )
