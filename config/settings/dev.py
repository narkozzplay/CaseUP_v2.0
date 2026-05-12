import os

from .base import *


def _normalize_allowed_host(host: str) -> str:
    normalized = (host or "").strip().lower()
    if not normalized:
        return ""

    if "://" in normalized:
        normalized = normalized.split("://", 1)[1]
    normalized = normalized.split("/", 1)[0]

    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized[1:].split("]", 1)[0]
    elif ":" in normalized:
        normalized = normalized.split(":", 1)[0]

    return normalized.lstrip(".")


def _is_allowed_dev_host(host: str) -> bool:
    normalized = _normalize_allowed_host(host)
    if not normalized:
        return True

    if normalized in {"127.0.0.1", "localhost", "::1"}:
        return True

    return normalized.endswith((".localhost", ".local", ".test", "ngrok-free.dev", "ngrok-free.app"))


DEBUG = True
SECRET_KEY = (os.getenv("DJANGO_SECRET_KEY") or "dev-secret-key-not-for-production").strip()
ALLOWED_HOSTS = get_env_list(
    "DJANGO_ALLOWED_HOSTS",
    "127.0.0.1,localhost,.ngrok-free.dev,.ngrok-free.app,192.168.0.19,caseup.local",
)
CSRF_TRUSTED_ORIGINS = get_env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "https://*.ngrok-free.dev,https://*.ngrok-free.app",
)

unsafe_hosts = [host for host in ALLOWED_HOSTS if not _is_allowed_dev_host(host)]
if unsafe_hosts:
    raise RuntimeError(
        "config.settings.dev can only be used with local or tunnel-only hosts. "
        "Set DJANGO_SETTINGS_MODULE=config.settings.prod for external deployments. "
        f"Unsafe hosts: {', '.join(unsafe_hosts)}"
    )
