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
