"""Picture for the channel post: the digest card rendered in headless Chromium and saved as a JPEG.

The card is rendered from the template straight into the browser (no request to the site), so it works without
the site being reachable from the worker. Item icons come from the Steam CDN; a missing icon leaves a gap.
"""
from __future__ import annotations

from django.template.loader import render_to_string

from .models import TelegramNotifierConfig

CARD_WIDTH = 1080
# Twice the pixels keep small text sharp after Telegram's recompression.
SCALE = 2
LOAD_TIMEOUT_MS = 15_000


class DigestImageError(RuntimeError):
    pass


def render_card_html(digest, *, config: TelegramNotifierConfig) -> str:
    from .channel_digest import card_context

    return render_to_string("tg_notifier/digest_card_page.html", card_context(digest, config=config))


def render_digest_image(digest, *, config: TelegramNotifierConfig) -> bytes:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DigestImageError("Playwright не установлен — пересоберите образ (docker compose ... --build).") from exc

    page_html = render_card_html(digest, config=config)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                page = browser.new_page(viewport={"width": CARD_WIDTH, "height": 800}, device_scale_factor=SCALE)
                try:
                    page.set_content(page_html, wait_until="networkidle", timeout=LOAD_TIMEOUT_MS)
                except PlaywrightTimeout:
                    pass  # Slow icons: the card is already laid out, take it as it is.
                return page.locator(".dg-card").screenshot(type="jpeg", quality=88)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise DigestImageError(f"Скриншот не получился: {exc}") from exc
