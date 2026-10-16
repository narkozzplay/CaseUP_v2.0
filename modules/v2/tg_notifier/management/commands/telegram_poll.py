"""Long-running bot poller (container telegram_poller): fetches /start messages through the relay.

Idles while the bot is off, has no token or works in webhook mode. Touches HEARTBEAT_FILE on every tick so the
container healthcheck can tell a hung poller from a working one.
"""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from modules.v2.tg_notifier.services import TelegramNotifierError
from modules.v2.tg_notifier.updates import poll_once

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = Path("/tmp/telegram_poll.alive")
IDLE_SECONDS = 30
MAX_BACKOFF_SECONDS = 120


class Command(BaseCommand):
    help = "Забирает сообщения бота из Telegram (режим «Опрос»)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Один опрос и выход.")

    def handle(self, *args, **options):
        self._stop = False
        if not options["once"]:
            signal.signal(signal.SIGTERM, self._request_stop)
            signal.signal(signal.SIGINT, self._request_stop)

        backoff = 5
        while not self._stop:
            close_old_connections()
            self._heartbeat()
            try:
                worked, handled = poll_once()
            except TelegramNotifierError as exc:
                logger.warning("telegram_poll: %s", exc)
                if options["once"]:
                    raise
                self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            except Exception:
                logger.exception("telegram_poll: unexpected error")
                if options["once"]:
                    raise
                self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            backoff = 5
            if handled:
                logger.info("telegram_poll: handled %s update(s)", handled)
            if options["once"]:
                self.stdout.write(f"handled={handled} worked={worked}")
                return
            if not worked:
                self._sleep(IDLE_SECONDS)

    def _request_stop(self, *_args):
        self._stop = True

    def _sleep(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            time.sleep(1)
            self._heartbeat()

    @staticmethod
    def _heartbeat() -> None:
        try:
            HEARTBEAT_FILE.touch()
        except OSError:
            pass
