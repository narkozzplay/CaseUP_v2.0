"""Pick'em: saving picks, scoring them against the results and granting rewards."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile

from .models import SWISS_PICK_LIMITS, BracketPick, Major, Match, RewardGrant, RewardRule, Stage, StageTeam, SwissPick

logger = logging.getLogger(__name__)

R = Match.Round


class PickemError(Exception):
    pass


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: str = ""
    action_url: str = ""
    action_label: str = ""


def current_major() -> Major | None:
    return Major.objects.filter(is_published=True).order_by("-starts_at").first()


def _is_banned(user) -> bool:
    from modules.v2.control_center.models import UserBan

    return UserBan.objects.active().filter(user=user).exists()


def _has_steam(user) -> bool:
    profile = UserProfile.objects.filter(user=user).only("steam_id").first()
    return bool(profile and (profile.steam_id or "").strip())


def eligibility(user, major: Major) -> Eligibility:
    if not getattr(user, "is_authenticated", False):
        return Eligibility(False, "Войдите через Steam, чтобы делать прогнозы.", reverse("accounts:login"), "Войти через Steam")
    if _is_banned(user):
        return Eligibility(False, "Аккаунт заблокирован.")
    if major.require_steam and not _has_steam(user):
        return Eligibility(False, "Pick'em доступен аккаунтам с привязанным Steam.", reverse("profiles:profile"), "Подключить Steam")
    return Eligibility(True)


# --------------------------------------------------------------------------- bracket shape


def feeders(round_no: int, slot: int) -> list[tuple[int, int]]:
    """The matches whose teams meet in this one: winners for semifinals and the final, semifinal losers for 3rd place."""
    if round_no == R.THIRD_PLACE:
        return [(R.SEMIFINAL, 1), (R.SEMIFINAL, 2)]
    if round_no <= R.QUARTERFINAL:
        return []
    return [(round_no - 1, 2 * slot - 1), (round_no - 1, 2 * slot)]


def bracket_slots(stage: Stage) -> list[tuple[int, int]]:
    """(round, slot) of every match the bracket asks to predict, in display order."""
    existing = set(stage.matches.values_list("round", "slot"))
    slots = [(R.QUARTERFINAL, s) for s in range(1, 5)] + [(R.SEMIFINAL, 1), (R.SEMIFINAL, 2), (R.FINAL, 1)]
    if (R.THIRD_PLACE, 1) in existing:
        slots.append((R.THIRD_PLACE, 1))
    return slots


def advance_winners(stage: Stage) -> None:
    """Fill the later playoff matches from the winners (and semifinal losers) entered so far."""
    matches = {(m.round, m.slot): m for m in stage.matches.select_related("team_a", "team_b", "winner")}
    for (round_no, slot) in [(R.SEMIFINAL, 1), (R.SEMIFINAL, 2), (R.FINAL, 1), (R.THIRD_PLACE, 1)]:
        match = matches.get((round_no, slot))
        if match is None:
            continue
        sources = [matches.get(key) for key in feeders(round_no, slot)]
        changed = False
        for attr, source in zip(("team_a", "team_b"), sources):
            if source is None:
                continue  # no feeder match: the side is entered by hand
            # A cleared feeder result clears this side too, so a stale winner never counts.
            team = source.loser if round_no == R.THIRD_PLACE else source.winner
            if getattr(match, f"{attr}_id") != (team.pk if team else None):
                setattr(match, attr, team)
                changed = True
        if changed:
            # New entrants make the old result meaningless, even if the winner is still one of them.
            match.winner, match.score = None, ""
            match.save()


# --------------------------------------------------------------------------- saving picks


def _check_open(user, stage: Stage) -> None:
    check = eligibility(user, stage.major)
    if not check.ok:
        raise PickemError(check.reason)
    if stage.is_locked:
        raise PickemError("Стадия уже началась, прогнозы закрыты.")


@transaction.atomic
def save_swiss_picks(user, stage: Stage, picks: dict[int, str]) -> int:
    """Replace the user's picks for a Swiss stage. ``picks`` maps team id to 3-0, advance or 0-3."""
    if not stage.is_swiss:
        raise PickemError("Это не швейцарская стадия.")
    _check_open(user, stage)
    team_ids = set(stage.entries.values_list("team_id", flat=True))
    counts: dict[str, int] = defaultdict(int)
    clean: dict[int, str] = {}
    for team_id, pick in picks.items():
        if not pick:
            continue
        if pick not in SWISS_PICK_LIMITS:
            raise PickemError("Неизвестный вариант прогноза.")
        if team_id not in team_ids:
            raise PickemError("Этой команды нет в стадии.")
        counts[pick] += 1
        clean[team_id] = pick
    for pick, limit in SWISS_PICK_LIMITS.items():
        if counts[pick] > limit:
            label = dict(SwissPick.Pick.choices)[pick]
            raise PickemError(f"«{label}»: можно выбрать не больше {limit} команд.")
    SwissPick.objects.filter(user=user, stage=stage).exclude(team_id__in=clean).delete()
    for team_id, pick in clean.items():
        SwissPick.objects.update_or_create(user=user, stage=stage, team_id=team_id, defaults={"pick": pick})
    return len(clean)


@transaction.atomic
def save_bracket_picks(user, stage: Stage, picks: dict[tuple[int, int], int]) -> int:
    """Replace the user's playoff bracket. ``picks`` maps (round, slot) to the team id they expect to win."""
    if stage.is_swiss:
        raise PickemError("Это не плей-офф.")
    _check_open(user, stage)
    matches = {(m.round, m.slot): m for m in stage.matches.all()}
    clean: dict[tuple[int, int], int] = {}
    for key in bracket_slots(stage):
        team_id = picks.get(key)
        if not team_id:
            continue
        round_no, slot = key
        if round_no == R.QUARTERFINAL:
            match = matches.get(key)
            options = {match.team_a_id, match.team_b_id} - {None} if match else set()
        elif round_no == R.THIRD_PLACE:
            semis = [clean.get(src) for src in feeders(R.FINAL, 1)]
            entrants = {clean.get(src) for semi in feeders(R.FINAL, 1) for src in feeders(*semi)}
            options = entrants - set(semis) - {None}
        else:
            options = {clean.get(src) for src in feeders(round_no, slot)} - {None}
        if team_id not in options:
            raise PickemError("Сетка заполнена неверно: команда не может выйти в этот матч.")
        clean[key] = team_id
    BracketPick.objects.filter(user=user, stage=stage).delete()
    BracketPick.objects.bulk_create([BracketPick(user=user, stage=stage, round=r, slot=s, team_id=t) for (r, s), t in clean.items()])
    return len(clean)


# --------------------------------------------------------------------------- scoring


@dataclass
class StageScore:
    stage: Stage
    picks: int = 0
    correct: int = 0
    decided: int = 0
    max_picks: int = 0
    by_round: dict = field(default_factory=dict)  # playoffs: round -> [correct, decided, total]

    @property
    def threshold(self) -> int:
        return min(self.stage.pass_correct, self.max_picks) if self.max_picks else self.stage.pass_correct

    @property
    def passed(self) -> bool | None:
        """None until the stage is settled."""
        if not self.stage.is_settled:
            return None
        return self.correct >= self.threshold

    @property
    def perfect(self) -> bool:
        return self.stage.is_settled and self.picks == self.max_picks and self.correct == self.max_picks


class MajorResults:
    """The results of one Major loaded once, to score any number of users against them."""

    def __init__(self, major: Major):
        self.major = major
        self.stages = list(major.stages.order_by("order"))
        self.entries: dict[int, dict[int, StageTeam]] = defaultdict(dict)
        for entry in StageTeam.objects.filter(stage__major=major):
            self.entries[entry.stage_id][entry.team_id] = entry
        self.matches: dict[int, dict[tuple[int, int], Match]] = defaultdict(dict)
        for match in Match.objects.filter(stage__major=major):
            self.matches[match.stage_id][(match.round, match.slot)] = match
        self.slots = {stage.pk: bracket_slots(stage) for stage in self.stages if not stage.is_swiss}

    def score(self, stage: Stage, swiss: dict[int, str], bracket: dict[tuple[int, int], int]) -> StageScore:
        result = StageScore(stage=stage)
        if stage.is_swiss:
            result.max_picks = sum(SWISS_PICK_LIMITS.values())
            entries = self.entries.get(stage.pk, {})
            for team_id, pick in swiss.items():
                result.picks += 1
                entry = entries.get(team_id)
                if entry is None or not entry.outcome:
                    continue
                result.decided += 1
                result.correct += int(entry.outcome == pick)
            return result
        slots = self.slots.get(stage.pk, [])
        result.max_picks = len(slots)
        matches = self.matches.get(stage.pk, {})
        for key in slots:
            tally = result.by_round.setdefault(key[0], [0, 0, 0])
            tally[2] += 1
            team_id = bracket.get(key)
            if team_id:
                result.picks += 1
            match = matches.get(key)
            if match is None or not match.winner_id:
                continue
            tally[1] += 1
            result.decided += 1
            if team_id == match.winner_id:
                tally[0] += 1
                result.correct += 1
        return result

    def round_decided(self, round_no: int) -> bool:
        for stage in self.stages:
            if stage.is_swiss:
                continue
            keys = [key for key in self.slots.get(stage.pk, []) if key[0] == round_no]
            matches = self.matches.get(stage.pk, {})
            return bool(keys) and all(matches.get(key) and matches[key].winner_id for key in keys)
        return False


def _load_picks(major: Major, user_ids=None):
    swiss: dict[int, dict[int, dict[int, str]]] = defaultdict(lambda: defaultdict(dict))
    bracket: dict[int, dict[int, dict[tuple[int, int], int]]] = defaultdict(lambda: defaultdict(dict))
    swiss_qs = SwissPick.objects.filter(stage__major=major)
    bracket_qs = BracketPick.objects.filter(stage__major=major)
    if user_ids is not None:
        swiss_qs, bracket_qs = swiss_qs.filter(user_id__in=user_ids), bracket_qs.filter(user_id__in=user_ids)
    for user_id, stage_id, team_id, pick in swiss_qs.values_list("user_id", "stage_id", "team_id", "pick"):
        swiss[user_id][stage_id][team_id] = pick
    for user_id, stage_id, round_no, slot, team_id in bracket_qs.values_list("user_id", "stage_id", "round", "slot", "team_id"):
        bracket[user_id][stage_id][(round_no, slot)] = team_id
    return swiss, bracket


@dataclass
class UserSummary:
    user_id: int
    stages: list[StageScore]

    @property
    def correct(self) -> int:
        return sum(score.correct for score in self.stages)

    @property
    def picks(self) -> int:
        return sum(score.picks for score in self.stages)

    @property
    def passed_stages(self) -> int:
        return sum(1 for score in self.stages if score.passed)

    def for_stage(self, stage_id: int) -> StageScore | None:
        return next((score for score in self.stages if score.stage.pk == stage_id), None)

    def playoff_round(self, round_no: int) -> tuple[int, int]:
        """(correct, total) for one playoff round."""
        for score in self.stages:
            if round_no in score.by_round:
                correct, _decided, total = score.by_round[round_no]
                return correct, total
        return 0, 0


def summaries(major: Major, user_ids=None, results: MajorResults | None = None) -> dict[int, UserSummary]:
    results = results or MajorResults(major)
    swiss, bracket = _load_picks(major, user_ids)
    out = {}
    for user_id in set(swiss) | set(bracket):
        out[user_id] = UserSummary(user_id, [results.score(stage, swiss[user_id].get(stage.pk, {}), bracket[user_id].get(stage.pk, {})) for stage in results.stages])
    return out


def user_summary(major: Major, user) -> UserSummary | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return summaries(major, [user.pk]).get(user.pk)


def leaderboard(major: Major, limit: int = 100) -> list[dict]:
    rows = sorted(summaries(major).values(), key=lambda s: (-s.correct, -s.passed_stages, -s.picks, s.user_id))[:limit]
    users = {user.pk: user for user in get_user_model().objects.filter(pk__in=[row.user_id for row in rows]).select_related("profile_v2")}
    board, place, previous = [], 0, None
    for index, row in enumerate(rows, start=1):
        key = (row.correct, row.passed_stages)
        if key != previous:
            place, previous = index, key
        user = users.get(row.user_id)
        board.append({"place": place, "user": user, "name": player_name(user), "avatar": player_avatar(user), "summary": row})
    return board


def _profile(user):
    try:
        return user.profile_v2
    except Exception:  # noqa: BLE001 - a user without a profile row
        return None


def player_name(user) -> str:
    if user is None:
        return "—"
    profile = _profile(user)
    return (profile and (profile.steam_personaname or "").strip()) or user.get_username()


def player_avatar(user) -> str:
    profile = _profile(user) if user is not None else None
    if profile is None:
        return ""
    if profile.uploaded_avatar:
        return profile.uploaded_avatar.url
    return profile.steam_avatar or ""


# --------------------------------------------------------------------------- rewards


def achieved_scopes(rule: RewardRule, summary: UserSummary, results: MajorResults) -> list[tuple[str, Stage | None]]:
    """The scopes (one-off, or one per stage) in which the user meets the rule."""
    stages = summary.stages
    all_settled = bool(stages) and all(score.stage.is_settled for score in stages)
    condition = rule.condition
    C = RewardRule.Condition
    if condition == C.ALL_STAGES:
        return [("", None)] if all_settled and all(score.passed for score in stages) else []
    if condition == C.PERFECT:
        return [("", None)] if all_settled and all(score.perfect for score in stages) else []
    if condition == C.STAGE_PASSED:
        out = []
        for score in stages:
            if rule.stage_id and score.stage.pk != rule.stage_id:
                continue
            if not score.stage.is_settled:
                continue
            need = rule.min_correct or score.threshold
            if score.correct >= need:
                out.append(("" if rule.stage_id else f"stage:{score.stage.pk}", score.stage))
        return out
    round_no = {C.SEMIFINALISTS: R.QUARTERFINAL, C.FINALISTS: R.SEMIFINAL, C.CHAMPION: R.FINAL}[condition]
    if not results.round_decided(round_no):
        return []
    correct, total = summary.playoff_round(round_no)
    need = 1 if condition == C.CHAMPION else (rule.min_correct or total)
    return [("", None)] if total and correct >= need else []


def _deliver(grant: RewardGrant) -> None:
    rule = grant.rule
    if rule.kind == RewardRule.Kind.BALANCE:
        from modules.v2.profiles.services.balance import adjust_balance

        entry = adjust_balance(
            user=grant.user,
            delta=rule.amount,
            reason=LedgerReason.MAJOR_PICKEM,
            amount_snapshot=rule.amount,
            idempotency_key=f"major_reward:{grant.pk}",
            comment=f"Pick'em {rule.major.name}: {rule.title}",
        )
        grant.ledger_entry_id = entry.pk
    elif rule.kind == RewardRule.Kind.SUBSCRIPTION:
        from modules.v2.subscriptions.services import grant_bonus_days

        grant_bonus_days(user=grant.user, plan=rule.plan, days=rule.days, note=f"Pick'em {rule.major.name}: {rule.title}")
    # A skin is sent by hand with a Steam trade; the grant waits in the admin until it is marked as sent.
    if rule.kind == RewardRule.Kind.ITEM:
        grant.status = RewardGrant.Status.TO_SEND if trade_url(grant.user) else RewardGrant.Status.NEEDS_TRADE_URL
    else:
        grant.status = RewardGrant.Status.GRANTED
    grant.decided_at = timezone.now()
    grant.save(update_fields=["status", "ledger_entry_id", "decided_at", "decided_by"])
    from .notifications import notify_reward

    notify_reward(grant)


@transaction.atomic
def approve_grant(grant: RewardGrant, *, by=None) -> bool:
    grant = RewardGrant.objects.select_for_update(of=("self",)).select_related("rule__major", "rule__plan", "user").get(pk=grant.pk)
    if grant.status != RewardGrant.Status.PENDING:
        return False
    grant.decided_by = by
    _deliver(grant)
    return True


@transaction.atomic
def mark_item_sent(grant: RewardGrant, *, by=None, note: str = "") -> bool:
    updated = RewardGrant.objects.filter(pk=grant.pk, status__in=(RewardGrant.Status.TO_SEND, RewardGrant.Status.NEEDS_TRADE_URL)).update(
        status=RewardGrant.Status.GRANTED, decided_at=timezone.now(), decided_by=by, note=note[:255],
    )
    return bool(updated)


def trade_url(user) -> str:
    from modules.v2.market.models import MarketSeller

    return MarketSeller.objects.filter(user=user).values_list("trade_url", flat=True).first() or ""


@transaction.atomic
def reject_grant(grant: RewardGrant, *, by=None, note: str = "") -> bool:
    updated = RewardGrant.objects.filter(pk=grant.pk, status__in=(RewardGrant.Status.PENDING, RewardGrant.Status.NEEDS_TRADE_URL, RewardGrant.Status.TO_SEND)).update(
        status=RewardGrant.Status.REJECTED, decided_at=timezone.now(), decided_by=by, note=note[:255],
    )
    return bool(updated)


@dataclass
class SettleReport:
    granted: int = 0
    pending: int = 0
    skipped: int = 0


def settle_major(major: Major) -> SettleReport:
    """Create grants for every rule the players now meet. Safe to run again: each grant exists once."""
    report = SettleReport()
    rules = list(major.reward_rules.filter(is_active=True).select_related("plan", "major", "item"))
    if not rules:
        return report
    results = MajorResults(major)
    all_summaries = summaries(major, results=results)
    users = {user.pk: user for user in get_user_model().objects.filter(pk__in=all_summaries.keys())}
    for user_id, summary in all_summaries.items():
        user = users.get(user_id)
        if user is None:
            continue
        allowed = None
        for rule in rules:
            for scope, stage in achieved_scopes(rule, summary, results):
                if RewardGrant.objects.filter(rule=rule, user=user, scope=scope).exists():
                    continue
                if allowed is None:
                    allowed = eligibility(user, major).ok
                if not allowed:
                    report.skipped += 1
                    continue
                with transaction.atomic():
                    grant, created = RewardGrant.objects.get_or_create(rule=rule, user=user, scope=scope, defaults={"stage": stage})
                    if not created:
                        continue
                    if rule.needs_approval:
                        report.pending += 1
                    else:
                        _deliver(grant)
                        report.granted += 1
    return report
