"""Control Center · Majors: majors, stages, teams, playoff results, Pick'em rewards and the prize queue."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.majors.forms import MajorForm, MatchFormSet, RewardRuleForm, StageForm, StageTeamFormSet, TeamForm
from modules.v2.majors.models import BracketPick, Major, RewardGrant, RewardRule, Stage, SwissPick, Team
from modules.v2.majors import services as pickem

from .models import AuditAction
from .services.audit import log_admin_action
from .views import _admin_access, _forbidden, _render, _safe_next

OPEN_GRANT_STATUSES = (RewardGrant.Status.PENDING, RewardGrant.Status.NEEDS_TRADE_URL, RewardGrant.Status.TO_SEND)


def _log(request, summary: str, **payload) -> None:
    log_admin_action(actor=request.user, action=AuditAction.MAJOR_UPDATE, summary=summary[:255], payload=payload, request=request)


def _int(raw) -> int:
    raw = str(raw or "")
    return int(raw) if raw.isdigit() else 0


def _participants(major: Major) -> int:
    swiss = SwissPick.objects.filter(stage__major=major).values_list("user_id", flat=True)
    bracket = BracketPick.objects.filter(stage__major=major).values_list("user_id", flat=True)
    return len(set(swiss) | set(bracket))


def _changed_labels(form) -> str:
    return ", ".join(str(form.fields[name].label) for name in form.changed_data if name in form.fields)


# --------------------------------------------------------------------------- list and prize queue


@login_required
def majors(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    rows = []
    for major in Major.objects.annotate(stages_count=Count("stages", distinct=True), rules_count=Count("reward_rules", distinct=True)):
        rows.append({"major": major, "participants": _participants(major)})
    grants = (
        RewardGrant.objects.filter(status__in=OPEN_GRANT_STATUSES)
        .select_related("rule", "rule__major", "rule__item", "rule__plan", "user", "user__profile_v2", "stage")
        .order_by("created_at", "id")[:200]
    )
    queue = [{"grant": grant, "trade_url": pickem.trade_url(grant.user) if grant.rule.kind == RewardRule.Kind.ITEM else ""} for grant in grants]
    totals = RewardGrant.objects.aggregate(
        pending=Count("id", filter=Q(status=RewardGrant.Status.PENDING)),
        to_send=Count("id", filter=Q(status__in=(RewardGrant.Status.TO_SEND, RewardGrant.Status.NEEDS_TRADE_URL))),
        granted=Count("id", filter=Q(status=RewardGrant.Status.GRANTED)),
    )
    context = {
        "role_access": role_access,
        "rows": rows,
        "queue": queue,
        "totals": totals,
        "teams_count": Team.objects.filter(is_active=True).count(),
        "current": pickem.current_major(),
    }
    return _render(request, partial="control_center/partials/majors_content.html", context=context, title="Мейджоры · Control Center", tab="majors")


@login_required
@require_POST
def major_grant_action(request, grant_id: int):
    if _admin_access(request) is None:
        return _forbidden()
    grant = get_object_or_404(RewardGrant.objects.select_related("rule", "rule__major", "user"), pk=grant_id)
    decision = request.POST.get("decision") or ""
    if decision == "approve":
        done, verb = pickem.approve_grant(grant, by=request.user), "выдана"
    elif decision == "sent":
        done, verb = pickem.mark_item_sent(grant, by=request.user, note=request.POST.get("note", "")), "отмечена отправленной"
    elif decision == "reject":
        done, verb = pickem.reject_grant(grant, by=request.user, note=(request.POST.get("note") or "Отклонено в Control Center")), "отклонена"
    else:
        done, verb = False, ""
    if done:
        _log(request, f"Pick'em {grant.rule.major.name}: награда «{grant.rule.title}» {verb} — {grant.user.username}", grant=grant.pk, decision=decision)
        messages.success(request, f"Награда «{grant.rule.title}» для {grant.user.username} {verb}.")
    else:
        messages.error(request, "Награду уже обработали или действие недоступно.")
    return redirect(_safe_next(request, reverse("control_center:majors") + "#prizes"))


# --------------------------------------------------------------------------- major


@login_required
def major_edit(request, major_id: int | None = None):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    major = get_object_or_404(Major, pk=major_id) if major_id else None
    if request.method == "POST" and major is not None and request.POST.get("action") in ("settle", "publish"):
        if request.POST["action"] == "settle":
            report = pickem.settle_major(major)
            summary = f"выдано {report.granted}, ждут проверки {report.pending}, пропущено (нет Steam или бан) {report.skipped}"
            _log(request, f"Pick'em {major.name}: итоги — {summary}", major=major.pk, granted=report.granted, pending=report.pending, skipped=report.skipped)
            messages.success(request, f"Итоги подведены: {summary}.")
        else:
            major.is_published = not major.is_published
            major.save(update_fields=["is_published"])
            state = "опубликован" if major.is_published else "скрыт"
            _log(request, f"Мейджор «{major.name}» {state}", major=major.pk)
            messages.success(request, f"Мейджор {state}.")
        return redirect("control_center:major_edit", major_id=major.pk)

    form = MajorForm(request.POST or None, request.FILES or None, instance=major)
    if request.method == "POST":
        if form.is_valid():
            is_new = major is None
            changed = _changed_labels(form)
            saved = form.save()
            _log(request, f"Мейджор «{saved.name}» {'создан' if is_new else 'изменён: ' + changed}", major=saved.pk, changed=sorted(form.changed_data))
            messages.success(request, f"Мейджор «{saved.name}» {'создан. Добавьте стадии и награды' if is_new else 'сохранён'}.")
            return redirect("control_center:major_edit", major_id=saved.pk)
        messages.error(request, "Проверьте поля формы.")

    context = {"role_access": role_access, "form": form, "major": major}
    if major is not None:
        context.update({
            "stages": major.stages.annotate(
                teams_count=Count("entries", distinct=True),
                matches_count=Count("matches", distinct=True),
                decided_count=Count("matches", filter=Q(matches__winner__isnull=False), distinct=True),
            ).order_by("order"),
            "rules": major.reward_rules.select_related("stage", "plan", "item").annotate(grants_count=Count("grants")),
            "participants": _participants(major),
            "open_grants": RewardGrant.objects.filter(rule__major=major, status__in=OPEN_GRANT_STATUSES).count(),
        })
    title = f"{major.name} · Мейджоры" if major else "Новый мейджор"
    return _render(request, partial="control_center/partials/major_edit_content.html", context=context, title=title, tab="majors")


# --------------------------------------------------------------------------- stage: teams, records, matches


@login_required
def stage_edit(request, major_id: int | None = None, stage_id: int | None = None):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    if stage_id:
        stage = get_object_or_404(Stage.objects.select_related("major"), pk=stage_id)
        major = stage.major
    else:
        stage = None
        major = get_object_or_404(Major, pk=major_id)
    action = (request.POST.get("action") or "save") if request.method == "POST" else ""

    if stage is not None and action in ("bracket", "carry_over", "delete"):
        if action == "bracket":
            try:
                added = pickem.create_bracket(stage, third_place=bool(request.POST.get("third_place")))
            except pickem.PickemError as exc:
                messages.error(request, str(exc))
                return redirect("control_center:stage_edit", stage_id=stage.pk)
            _log(request, f"{stage}: сетка плей-офф, добавлено матчей {added}", stage=stage.pk, added=added)
            messages.success(request, f"Добавлено матчей: {added}." if added else "Сетка уже заполнена.")
        elif action == "carry_over":
            try:
                added = pickem.carry_over_teams(stage)
            except pickem.PickemError as exc:
                messages.error(request, str(exc))
                return redirect("control_center:stage_edit", stage_id=stage.pk)
            _log(request, f"{stage}: перенесены команды из прошлой стадии ({added})", stage=stage.pk, added=added)
            messages.success(request, f"Добавлено команд: {added}." if added else "Новых прошедших команд нет — проверьте итоги прошлой стадии.")
        else:
            if stage.swiss_picks.exists() or stage.bracket_picks.exists():
                messages.error(request, "На стадию уже есть прогнозы — удалить её нельзя.")
                return redirect("control_center:stage_edit", stage_id=stage.pk)
            name = str(stage)
            try:
                stage.delete()
            except ProtectedError:
                messages.error(request, "По наградам этой стадии уже есть выдачи — удалить её нельзя.")
                return redirect("control_center:stage_edit", stage_id=stage.pk)
            _log(request, f"Стадия «{name}» удалена", major=major.pk)
            messages.success(request, f"Стадия «{name}» удалена.")
            return redirect("control_center:major_edit", major_id=major.pk)
        return redirect("control_center:stage_edit", stage_id=stage.pk)

    data = request.POST if action == "save" else None
    form = StageForm(data, instance=stage, major=major)
    teams_formset = StageTeamFormSet(data, instance=stage or Stage(major=major), prefix="teams")
    matches_formset = MatchFormSet(data, instance=stage or Stage(major=major), prefix="matches") if stage is not None and not stage.is_swiss else None
    if action == "save":
        forms_ok = form.is_valid() and teams_formset.is_valid() and (matches_formset is None or matches_formset.is_valid())
        if forms_ok:
            is_new = stage is None
            saved = form.save()
            teams_formset.instance = saved
            teams_formset.save()
            if matches_formset is not None:
                matches_formset.save()
            if not saved.is_swiss:
                # Winners move on to the next round by themselves, as in the Django admin.
                pickem.advance_winners(saved)
            changed = [name for name, part in (("стадия", form), ("команды", teams_formset), ("матчи", matches_formset)) if part is not None and part.has_changed()]
            _log(request, f"{saved}: {'создана' if is_new else 'изменено — ' + ', '.join(changed or ['ничего'])}", stage=saved.pk)
            messages.success(request, f"Стадия «{saved.name}» {'создана' if is_new else 'сохранена'}.")
            return redirect("control_center:stage_edit", stage_id=saved.pk)
        messages.error(request, "Проверьте поля формы.")

    context = {
        "role_access": role_access,
        "major": major,
        "stage": stage,
        "form": form,
        "teams_formset": teams_formset,
        "matches_formset": matches_formset,
        "has_previous": stage is not None and major.stages.filter(order__lt=stage.order).exists(),
        "picks_count": (stage.swiss_picks.values("user_id").distinct().count() + stage.bracket_picks.values("user_id").distinct().count()) if stage else 0,
    }
    title = f"{stage.name} · {major.name}" if stage else f"Новая стадия · {major.name}"
    return _render(request, partial="control_center/partials/major_stage_content.html", context=context, title=title, tab="majors")


# --------------------------------------------------------------------------- reward rules


@login_required
def reward_edit(request, major_id: int | None = None, rule_id: int | None = None):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    if rule_id:
        rule = get_object_or_404(RewardRule.objects.select_related("major", "item"), pk=rule_id)
        major = rule.major
    else:
        rule = None
        major = get_object_or_404(Major, pk=major_id)

    if request.method == "POST" and rule is not None and request.POST.get("action") == "delete":
        title = rule.title
        try:
            rule.delete()
        except ProtectedError:
            messages.error(request, "По награде уже есть выдачи — удалить нельзя, выключите её.")
            return redirect("control_center:reward_edit", rule_id=rule.pk)
        _log(request, f"Pick'em {major.name}: награда «{title}» удалена", major=major.pk)
        messages.success(request, f"Награда «{title}» удалена.")
        return redirect(reverse("control_center:major_edit", args=[major.pk]) + "#rewards")

    form = RewardRuleForm(request.POST or None, instance=rule, major=major)
    if request.method == "POST":
        if form.is_valid():
            is_new = rule is None
            changed = _changed_labels(form)
            saved = form.save()
            _log(request, f"Pick'em {major.name}: награда «{saved.title}» {'создана' if is_new else 'изменена: ' + changed}", rule=saved.pk, changed=sorted(form.changed_data))
            messages.success(request, f"Награда «{saved.title}» {'создана' if is_new else 'сохранена'}.")
            return redirect(reverse("control_center:major_edit", args=[major.pk]) + "#rewards")
        messages.error(request, "Проверьте поля формы.")
    context = {"role_access": role_access, "major": major, "rule": rule, "form": form}
    title = f"{rule.title} · {major.name}" if rule else f"Новая награда · {major.name}"
    return _render(request, partial="control_center/partials/major_reward_content.html", context=context, title=title, tab="majors")


# --------------------------------------------------------------------------- teams


@login_required
def teams(request):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    q = (request.GET.get("q") or "").strip()
    qs = Team.objects.annotate(stages_count=Count("stage_entries", distinct=True))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(tag__icontains=q))
    context = {"role_access": role_access, "teams": qs.order_by("-is_active", "name"), "q": q}
    return _render(request, partial="control_center/partials/major_teams_content.html", context=context, title="Команды · Мейджоры", tab="majors")


@login_required
def team_edit(request, team_id: int | None = None):
    role_access = _admin_access(request)
    if role_access is None:
        return _forbidden()
    team = get_object_or_404(Team, pk=team_id) if team_id else None
    form = TeamForm(request.POST or None, request.FILES or None, instance=team)
    if request.method == "POST":
        if form.is_valid():
            is_new = team is None
            changed = _changed_labels(form)
            saved = form.save()
            _log(request, f"Команда «{saved.name}» {'добавлена' if is_new else 'изменена: ' + changed}", team=saved.pk, changed=sorted(form.changed_data))
            messages.success(request, f"Команда «{saved.name}» {'добавлена' if is_new else 'сохранена'}.")
            if is_new and request.POST.get("another"):
                return redirect("control_center:team_create")
            return redirect("control_center:major_teams")
        messages.error(request, "Проверьте поля формы.")
    context = {"role_access": role_access, "form": form, "team": team}
    title = f"{team.name} · Команды" if team else "Новая команда"
    return _render(request, partial="control_center/partials/major_team_edit_content.html", context=context, title=title, tab="majors")
