"""Error journal: every error of the site and of background tasks lands in ErrorGroup.

Errors are grouped by exception type and the place in our code where it was raised, so a broken page hit
a thousand times is one row with count=1000. Sources:

- ErrorJournalHandler — a logging handler (ERROR and above) on the root and django.request loggers:
  500s on the site and logger.exception(...) anywhere in our code;
- the Celery task_failure signal — tasks that crashed.

Writing must never break the code that failed, so everything here swallows its own errors.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import traceback
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Case, F, Value, When
from django.utils import timezone

_guard = threading.local()

# Noise that is not a bug of ours: scanners hitting the server with a wrong Host header.
IGNORED_LOGGERS = ("django.security.DisallowedHost", "django.db.backends")
# Celery logs crashed tasks itself; those come through the task_failure signal with the task name.
SIGNAL_HANDLED_LOGGERS = ("celery",)
TRACEBACK_LIMIT = 8000


def _project_root() -> str:
    return str(Path(settings.BASE_DIR).resolve())


def _our_frame(tb) -> traceback.FrameSummary | None:
    """Innermost frame in project code (not in site-packages): where it actually broke for us."""
    root = _project_root()
    frames = [frame for frame in traceback.extract_tb(tb) if frame.filename.startswith(root) and "site-packages" not in frame.filename]
    return frames[-1] if frames else None


def _relative(path: str) -> str:
    root = _project_root()
    return path[len(root) + 1:] if path.startswith(root) else path


def describe(exc_info=None, message: str = "", logger_name: str = "") -> dict:
    """fingerprint / title / location / traceback for an exception or a plain error message."""
    if exc_info and exc_info[0] is not None:
        exc_type, exc, tb = exc_info
        frame = _our_frame(tb)
        if frame is None:  # raised outside project code: name the last frame there is
            frames = traceback.extract_tb(tb)
            frame = frames[-1] if frames else None
        location = f"{_relative(frame.filename)}:{frame.name}" if frame else (logger_name or exc_type.__module__)
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        title = f"{exc_type.__name__}: {text}" if text else exc_type.__name__
        key = f"{exc_type.__module__}.{exc_type.__name__}|{location}"
        trace = "".join(traceback.format_exception(exc_type, exc, tb))
    else:
        location = logger_name
        title = message.strip().splitlines()[0] if message.strip() else "Ошибка без текста"
        key = f"{logger_name}|{title[:120]}"
        trace = message
    return {
        "fingerprint": hashlib.sha256(key.encode("utf-8", "replace")).hexdigest(),
        "title": title[:300],
        "location": location[:300],
        "traceback": trace[-TRACEBACK_LIMIT:],
    }


def record_error(*, source: str, exc_info=None, message: str = "", context: str = "", logger_name: str = ""):
    """Adds one occurrence. Returns the ErrorGroup, or None when it could not be written."""
    if getattr(_guard, "busy", False):
        return None
    _guard.busy = True
    try:
        # Inside a transaction that already failed nothing can be written; the console log still has it.
        if connection.in_atomic_block and connection.needs_rollback:
            return None
        from .models import ErrorGroup

        info = describe(exc_info, message, logger_name)
        now = timezone.now()
        context = (context or "")[:300]
        fields = {
            "count": F("count") + 1,
            "last_seen": now,
            "last_traceback": info["traceback"],
            "last_context": context,
            "title": info["title"],
            # A resolved error that comes back is open again and worth a new alert.
            "alerted_at": Case(When(resolved_at__isnull=False, then=Value(None)), default=F("alerted_at")),
            "resolved_at": None,
        }
        with transaction.atomic():
            if ErrorGroup.objects.filter(fingerprint=info["fingerprint"]).update(**fields):
                return ErrorGroup.objects.get(fingerprint=info["fingerprint"])
            try:
                with transaction.atomic():
                    return ErrorGroup.objects.create(
                        fingerprint=info["fingerprint"],
                        source=source,
                        title=info["title"],
                        location=info["location"],
                        last_context=context,
                        last_traceback=info["traceback"],
                        last_seen=now,
                    )
            except IntegrityError:  # the same error was just written by a parallel request
                ErrorGroup.objects.filter(fingerprint=info["fingerprint"]).update(**fields)
                return ErrorGroup.objects.get(fingerprint=info["fingerprint"])
    except Exception:  # noqa: BLE001 - the journal must never make things worse
        return None
    finally:
        _guard.busy = False


class ErrorJournalHandler(logging.Handler):
    def __init__(self, level=logging.ERROR):
        super().__init__(level=level)

    def emit(self, record: logging.LogRecord) -> None:
        name = record.name or ""
        if name.startswith(IGNORED_LOGGERS) or name.startswith(SIGNAL_HANDLED_LOGGERS):
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        request = getattr(record, "request", None)
        if request is not None or name.startswith("django.request"):
            source = "web"
            context = f"{getattr(request, 'method', '')} {getattr(request, 'path', '')}".strip()
        else:
            source = "other"
            context = name
        exc_info = record.exc_info if record.exc_info and record.exc_info[0] else None
        # Without an exception the message itself identifies the error; group by the unformatted template
        # so "failed for item 17" and "failed for item 18" are one row.
        if exc_info is None:
            message = str(record.msg) if isinstance(record.msg, str) else message
        record_error(source=source, exc_info=exc_info, message=message, context=context, logger_name=name)


def record_task_failure(sender=None, exception=None, traceback=None, **_kwargs) -> None:
    """Celery task_failure receiver."""
    if exception is None:
        return
    record_error(
        source="task",
        exc_info=(type(exception), exception, traceback),
        context=getattr(sender, "name", "") or "",
        logger_name=getattr(sender, "name", "") or "celery",
    )
