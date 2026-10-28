import os
from pathlib import Path

from .base import *

DEBUG = True
SECRET_KEY = "testing-secret-key"
ALLOWED_HOSTS = ["*"]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS = 300

# Admin tests log in with force_login; the 2FA tests turn the requirement back on themselves.
TWO_FACTOR_REQUIRED_FOR_ADMINS = False
# Analytics tests turn the counter on with override_settings.
YANDEX_METRIKA_ID = ""

# Tests never depend on a running Redis and keep their output quiet (assertLogs still works).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
LOGGING["root"]["level"] = "CRITICAL"
LOGGING["loggers"]["django"]["level"] = "CRITICAL"
LOGGING["loggers"]["django.request"]["level"] = "CRITICAL"

BASE_DIR = Path(__file__).resolve().parent.parent.parent

TEST_DB_ENGINE = os.getenv("TEST_DB_ENGINE", "sqlite").strip().lower()

if TEST_DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("TEST_POSTGRES_DB", os.getenv("POSTGRES_DB", "caseup_test")),
            "USER": os.getenv("TEST_POSTGRES_USER", os.getenv("POSTGRES_USER", "caseup")),
            "PASSWORD": os.getenv("TEST_POSTGRES_PASSWORD", os.getenv("POSTGRES_PASSWORD", "caseup")),
            "HOST": os.getenv("TEST_POSTGRES_HOST", os.getenv("POSTGRES_HOST", "127.0.0.1")),
            "PORT": os.getenv("TEST_POSTGRES_PORT", os.getenv("POSTGRES_PORT", "5432")),
            "CONN_MAX_AGE": 0,
            "TEST": {
                "NAME": os.getenv("TEST_POSTGRES_DB", os.getenv("POSTGRES_DB", "caseup_test")),
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test_db.sqlite3",
        }
    }
# Tests never talk to a real payment gateway.
PAYMENTS_PROVIDER = "auto"
YOOKASSA_SHOP_ID = ""
YOOKASSA_SECRET_KEY = ""
FREEKASSA_MERCHANT_ID = ""
FREEKASSA_SECRET_1 = ""
FREEKASSA_SECRET_2 = ""
FREEKASSA_API_KEY = ""
# The test runner forces DEBUG=False, which would switch the "auto" source-IP check on for test clients.
PAYMENTS_WEBHOOK_IP_CHECK = "off"

# Price queue tests never sleep between Steam requests.
STEAM_PRICE_REQUEST_INTERVAL = 0

# Guest rate limit and page cache are tested on their own (override_settings); elsewhere they would make
# tests depend on how many requests ran before them.
GUEST_RATE_LIMIT_REQUESTS = 0
STEAM_CATALOG_GUEST_CACHE_SECONDS = 0
