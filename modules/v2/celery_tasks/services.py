from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.v2.steam_catalog.services import (
    detect_price_drop_alerts_for_category,
    evaluate_user_price_alerts_for_item_type,
)
from modules.v2.steam_catalog.services.price_queue import batch_limit, refresh_batch

from .models import PriceAutoUpdateRule, PriceAutoUpdateRunLog


@dataclass(frozen=True)
class RuleRunSummary:
    rule_id: int
    category: str
    status: str
    touched_items_count: int
    updated_items_count: int
    history_written_count: int
    errors_count: int


def _get_running_state_stale_after() -> timedelta:
    seconds = int(getattr(settings, "CELERY_RULE_RUNNING_STALE_SECONDS", "3600") or 3600)
    if seconds < 60:
        seconds = 60
    return timedelta(seconds=seconds)


def _is_rule_running_state_stale(rule: PriceAutoUpdateRule, *, now=None) -> bool:
    now = now or timezone.now()
    reference_dt = rule.updated_at or rule.last_run_at or rule.created_at
    return reference_dt <= now - _get_running_state_stale_after()


def rebuild_rule_schedule(rule: PriceAutoUpdateRule) -> None:
    if not rule.is_enabled:
        clear_rule_schedule(rule)
        return

    rule.schedule_initial_run()
    rule.save(update_fields=["next_run_at", "updated_at"])


def mark_rule_due_now(rule: PriceAutoUpdateRule) -> None:
    rule.next_run_at = timezone.now()
    rule.save(update_fields=["next_run_at", "updated_at"])


def clear_rule_schedule(rule: PriceAutoUpdateRule) -> None:
    rule.next_run_at = None
    if rule.last_status == PriceAutoUpdateRule.Status.RUNNING:
        rule.last_status = PriceAutoUpdateRule.Status.IDLE
    rule.save(update_fields=["next_run_at", "last_status", "updated_at"])


def get_due_rules(*, now=None):
    now = now or timezone.now()
    return (
        PriceAutoUpdateRule.objects
        .filter(is_enabled=True)
        .filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
        .order_by("next_run_at", "category")
    )


def _create_run_log(
    *,
    rule: PriceAutoUpdateRule,
    triggered_by: str,
    started_at,
    status: str,
    error_summary: str = "",
) -> PriceAutoUpdateRunLog:
    return PriceAutoUpdateRunLog.objects.create(
        rule=rule,
        category=rule.category,
        triggered_by=(triggered_by or "").strip()[:16],
        status=status,
        error_summary=error_summary,
        started_at=started_at,
        finished_at=started_at if status != PriceAutoUpdateRule.Status.RUNNING else None,
    )


def _finalize_run_log(
    run_log: PriceAutoUpdateRunLog,
    *,
    status: str,
    finished_at,
    touched_items_count: int,
    updated_items_count: int,
    history_written_count: int,
    errors_count: int,
    error_summary: str,
) -> None:
    run_log.status = status
    run_log.finished_at = finished_at
    run_log.touched_items_count = touched_items_count
    run_log.updated_items_count = updated_items_count
    run_log.history_written_count = history_written_count
    run_log.errors_count = errors_count
    run_log.error_summary = error_summary
    run_log.save(
        update_fields=[
            "status",
            "finished_at",
            "touched_items_count",
            "updated_items_count",
            "history_written_count",
            "errors_count",
            "error_summary",
        ]
    )


def run_price_auto_update_rule(rule_id: int, *, triggered_by: str = "beat") -> RuleRunSummary:
    started_at = timezone.now()
    run_log: PriceAutoUpdateRunLog | None = None
    touched_items_count = 0
    updated_items_count = 0
    history_written_count = 0

    try:
        with transaction.atomic():
            rule = PriceAutoUpdateRule.objects.select_for_update().get(pk=rule_id)

            if rule.last_status == PriceAutoUpdateRule.Status.RUNNING:
                if not _is_rule_running_state_stale(rule, now=started_at):
                    _create_run_log(
                        rule=rule,
                        triggered_by=triggered_by,
                        started_at=started_at,
                        status=PriceAutoUpdateRule.Status.RUNNING,
                        error_summary="Skipped: previous run is still active.",
                    )
                    return RuleRunSummary(
                        rule_id=rule.id,
                        category=rule.category,
                        status=PriceAutoUpdateRule.Status.RUNNING,
                        touched_items_count=0,
                        updated_items_count=0,
                        history_written_count=0,
                        errors_count=0,
                    )

                _create_run_log(
                    rule=rule,
                    triggered_by=triggered_by,
                    started_at=started_at,
                    status=PriceAutoUpdateRule.Status.ERROR,
                    error_summary="Recovered stale RUNNING state after interrupted worker.",
                )

            if triggered_by != "manual" and not rule.is_enabled:
                _create_run_log(
                    rule=rule,
                    triggered_by=triggered_by,
                    started_at=started_at,
                    status=PriceAutoUpdateRule.Status.IDLE,
                    error_summary="Skipped: rule is disabled.",
                )
                return RuleRunSummary(
                    rule_id=rule.id,
                    category=rule.category,
                    status=PriceAutoUpdateRule.Status.IDLE,
                    touched_items_count=0,
                    updated_items_count=0,
                    history_written_count=0,
                    errors_count=0,
                )

            rule.last_status = PriceAutoUpdateRule.Status.RUNNING
            rule.last_trigger = (triggered_by or "").strip()[:16]
            rule.last_error = ""
            rule.save(update_fields=["last_status", "last_trigger", "last_error", "updated_at"])

            run_log = _create_run_log(
                rule=rule,
                triggered_by=triggered_by,
                started_at=started_at,
                status=PriceAutoUpdateRule.Status.RUNNING,
            )

        # A small batch of the stalest items (hot ones first) instead of the whole category:
        # the catalog is far bigger than what Steam answers in one run.
        batch = refresh_batch(category=rule.category, limit=batch_limit(rule.interval_minutes), source=f"celery:{rule.category}")
        errors: list[str] = list(batch.errors)
        touched_items_count = batch.checked
        updated_items_count = batch.updated
        history_written_count = batch.history_written

        try:
            detect_price_drop_alerts_for_category(
                rule.category,
                trigger_source=triggered_by,
                item_ids=batch.changed_ids,
            )
            evaluate_user_price_alerts_for_item_type(
                rule.category,
                trigger_source=triggered_by,
            )
        except Exception as exc:
            errors.append(f"[alert-detector] {exc}")

        if errors and updated_items_count == 0 and history_written_count == 0 and touched_items_count == 0:
            final_status = PriceAutoUpdateRule.Status.ERROR
        elif errors:
            final_status = PriceAutoUpdateRule.Status.PARTIAL
        else:
            final_status = PriceAutoUpdateRule.Status.SUCCESS

        finished_at = timezone.now()
        error_summary = "\n".join(errors[:20])

        with transaction.atomic():
            locked_rule = PriceAutoUpdateRule.objects.select_for_update().get(pk=rule_id)
            locked_rule.last_run_at = finished_at
            locked_rule.touched_items_count = touched_items_count
            locked_rule.updated_items_count = updated_items_count
            locked_rule.history_written_count = history_written_count
            locked_rule.last_status = final_status
            locked_rule.last_error = error_summary
            if locked_rule.is_enabled:
                locked_rule.schedule_next_run(from_dt=locked_rule.last_run_at)
            else:
                locked_rule.next_run_at = None
            locked_rule.save(
                update_fields=[
                    "last_run_at",
                    "touched_items_count",
                    "updated_items_count",
                    "history_written_count",
                    "last_status",
                    "last_error",
                    "next_run_at",
                    "updated_at",
                ]
            )

        if run_log is not None:
            _finalize_run_log(
                run_log,
                status=final_status,
                finished_at=finished_at,
                touched_items_count=touched_items_count,
                updated_items_count=updated_items_count,
                history_written_count=history_written_count,
                errors_count=len(errors),
                error_summary=error_summary,
            )

        return RuleRunSummary(
            rule_id=rule.id,
            category=rule.category,
            status=final_status,
            touched_items_count=touched_items_count,
            updated_items_count=updated_items_count,
            history_written_count=history_written_count,
            errors_count=len(errors),
        )
    except Exception as exc:
        finished_at = timezone.now()

        with transaction.atomic():
            locked_rule = PriceAutoUpdateRule.objects.select_for_update().get(pk=rule_id)
            locked_rule.last_run_at = finished_at
            locked_rule.last_status = PriceAutoUpdateRule.Status.ERROR
            locked_rule.last_error = str(exc)
            if locked_rule.is_enabled:
                locked_rule.schedule_next_run(from_dt=locked_rule.last_run_at)
            else:
                locked_rule.next_run_at = None
            locked_rule.save(
                update_fields=[
                    "last_run_at",
                    "last_status",
                    "last_error",
                    "next_run_at",
                    "updated_at",
                ]
            )

        if run_log is not None:
            _finalize_run_log(
                run_log,
                status=PriceAutoUpdateRule.Status.ERROR,
                finished_at=finished_at,
                touched_items_count=touched_items_count,
                updated_items_count=updated_items_count,
                history_written_count=history_written_count,
                errors_count=1,
                error_summary=str(exc),
            )

        raise


def run_due_rules(*, triggered_by: str = "beat") -> list[RuleRunSummary]:
    summaries: list[RuleRunSummary] = []

    for rule in get_due_rules():
        summaries.append(run_price_auto_update_rule(rule.id, triggered_by=triggered_by))

    return summaries