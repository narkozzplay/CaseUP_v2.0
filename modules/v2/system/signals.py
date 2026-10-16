from __future__ import annotations

from django.db.models.signals import post_migrate
from django.db.utils import OperationalError, ProgrammingError
from django.dispatch import receiver

from .apps import SystemConfig
from .services import bootstrap_versioning_once


@receiver(post_migrate, dispatch_uid="caseup_system_bootstrap_versioning")
def bootstrap_versioning_after_migrate(sender, **_kwargs):
    if sender is not None and sender.label != SystemConfig.label:
        return
    try:
        bootstrap_versioning_once()
    except (OperationalError, ProgrammingError):
        # Keep migrate flow resilient if versioning tables are not fully ready yet.
        return


def _connect_error_journal():
    """Crashed Celery tasks go to the error journal; the worker re-creates its loggers, so the journal
    handler is attached again after Celery's own logging setup."""
    from celery.signals import after_setup_logger, task_failure

    from .errors import ErrorJournalHandler, record_task_failure

    task_failure.connect(record_task_failure, dispatch_uid="caseup_error_journal_task_failure", weak=False)

    def _attach(logger=None, **_kwargs):
        if logger is not None and not any(isinstance(h, ErrorJournalHandler) for h in logger.handlers):
            logger.addHandler(ErrorJournalHandler())

    after_setup_logger.connect(_attach, dispatch_uid="caseup_error_journal_logger", weak=False)


_connect_error_journal()


def _register_notification_kinds():
    from modules.v2.notifications.registry import payload_renderer, register_kind

    from .alerts import ALERT_KIND

    # Admin alerts (watchdog, error journal); push category "other" — admins cannot switch it off.
    register_kind(ALERT_KIND, payload_renderer("warn", "/control-center/health/"))


_register_notification_kinds()
