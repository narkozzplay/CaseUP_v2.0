"""Docker healthcheck of celery_beat: exits 0 while a `celery ... beat` process is running.

The schedule file stays on the volume after beat dies, and the image has no pgrep, so the process list is
read from /proc. Arguments are compared exactly, so this script (or a shell that runs it) never matches itself.
"""

import os
import sys


def beat_running() -> bool:
    me = str(os.getpid())
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                args = [arg for arg in handle.read().split(b"\0") if arg]
        except OSError:  # the process ended while we looked
            continue
        if b"beat" in args and any(os.path.basename(arg) == b"celery" or arg == b"celery" for arg in args):
            return True
    return False


if __name__ == "__main__":
    sys.exit(0 if beat_running() else 1)
