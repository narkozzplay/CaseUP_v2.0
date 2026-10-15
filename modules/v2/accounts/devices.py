"""Tiny User-Agent parser for the "active sessions" list: device type, OS and browser.

It only needs to be good enough for a person to recognise their own device, so it covers the
browsers and systems people actually use instead of pulling in a full UA database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DESKTOP = "desktop"
MOBILE = "mobile"
TABLET = "tablet"
BOT = "bot"

DEVICE_LABELS = {DESKTOP: "Компьютер", MOBILE: "Телефон", TABLET: "Планшет", BOT: "Бот", "": "Устройство"}

_WINDOWS_VERSIONS = {"10.0": "Windows", "6.3": "Windows 8.1", "6.2": "Windows 8", "6.1": "Windows 7"}

# Order matters: many browsers also carry "Chrome" and "Safari" tokens in their UA.
_BROWSERS = (
    ("Яндекс Браузер", re.compile(r"YaBrowser/(\d+)")),
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/(\d+)")),
    ("Opera", re.compile(r"(?:OPR|Opera)/(\d+)")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/(\d+)")),
    ("Steam", re.compile(r"Valve Steam|SteamWebHelper")),
    ("Firefox", re.compile(r"(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+)[\d.]* (?:Mobile/\w+ )?Safari/")),
)


@dataclass(frozen=True)
class DeviceInfo:
    device_type: str
    os: str
    browser: str

    @property
    def label(self) -> str:
        return " · ".join(part for part in (self.os, self.browser) if part) or "Неизвестное устройство"


def _parse_os(ua: str) -> str:
    if match := re.search(r"Windows NT ([\d.]+)", ua):
        return _WINDOWS_VERSIONS.get(match.group(1), "Windows")
    if "iPad" in ua:
        return "iPadOS"
    if match := re.search(r"iPhone OS (\d+)", ua):
        return f"iOS {match.group(1)}"
    if match := re.search(r"Android (\d+)", ua):
        return f"Android {match.group(1)}"
    if "Android" in ua:
        return "Android"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Mac OS X" in ua or "Macintosh" in ua:
        return "macOS"
    if "Linux" in ua:
        return "Linux"
    return ""


def _parse_browser(ua: str) -> str:
    for name, pattern in _BROWSERS:
        match = pattern.search(ua)
        if match:
            version = match.group(1) if match.groups() else ""
            return f"{name} {version}".strip()
    return ""


def _parse_device_type(ua: str) -> str:
    lowered = ua.lower()
    if any(token in lowered for token in ("bot", "crawl", "spider", "slurp", "curl/", "python-requests", "wget/")):
        return BOT
    if "ipad" in lowered or "tablet" in lowered or ("android" in lowered and "mobile" not in lowered):
        return TABLET
    if "mobi" in lowered or "iphone" in lowered or "android" in lowered:
        return MOBILE
    return DESKTOP if ua else ""


def parse_user_agent(user_agent: str | None) -> DeviceInfo:
    ua = (user_agent or "").strip()
    if not ua:
        return DeviceInfo("", "", "")
    return DeviceInfo(_parse_device_type(ua), _parse_os(ua), _parse_browser(ua))
