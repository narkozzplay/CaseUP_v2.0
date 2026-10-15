import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")

ENV_FILE_BY_SETTINGS_MODULE = {
    "config.settings.dev": ".env.dev",
    "config.settings.prod": ".env.prod",
    "config.settings.testing": ".env.test",
}

settings_module = os.getenv("DJANGO_SETTINGS_MODULE", "").strip()
env_filename = ENV_FILE_BY_SETTINGS_MODULE.get(settings_module)
if env_filename:
    load_env_file(BASE_DIR / env_filename)



def get_env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
DEBUG = False
ALLOWED_HOSTS = get_env_list("DJANGO_ALLOWED_HOSTS", "")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "social_django",
]

LOCAL_APPS = [
    "frontend",
]


PROJECT_APPS = [
    "modules.v2.accounts.apps.AccountsConfig",
    "modules.v2.system.apps.SystemConfig",
    "modules.v2.profiles.apps.ProfilesConfig",
    "modules.v2.social.apps.SocialConfig",
    "modules.v2.social_friends.apps.SocialFriendsConfig",
    "modules.v2.notifications.apps.NotificationsConfig",
    "modules.v2.profile_customization.apps.ProfileCustomizationConfig",
    "modules.v2.cs2_stats.apps.CS2StatsConfig",
    "modules.v2.subscriptions.apps.SubscriptionsConfig",
    "modules.v2.steam_catalog.apps.SteamCatalogConfig",
    "modules.v2.farm.apps.FarmConfig",
    "modules.v2.tradehub.apps.TradehubConfig",
    "modules.v2.steam_trading.apps.SteamTradingConfig",
    "modules.v2.celery_tasks.apps.CeleryTasksConfig",
    "modules.v2.control_center.apps.ControlCenterConfig",
    "modules.v2.tg_notifier.apps.TgNotifierConfig",
    "modules.v2.game_servers.apps.GameServersConfig",
    "modules.v2.case_simulator.apps.CaseSimulatorConfig",
    "modules.v2.payments.apps.PaymentsConfig",
    "modules.v2.store.apps.StoreConfig",
    "modules.v2.web_constructor.apps.WebConstructorConfig",
]

PLUGIN_APPS = []

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS + PROJECT_APPS + PLUGIN_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "modules.v2.system.request_cache.RequestCacheMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "frontend.middleware.CustomNotFoundPageMiddleware",
    "frontend.middleware.CustomForbiddenPageMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "modules.v2.control_center.middleware.ActivityContextMiddleware",
    "modules.v2.accounts.middleware.UserSessionMiddleware",
    "modules.v2.accounts.middleware.TwoFactorMiddleware",
    "modules.v2.system.middleware.MaintenanceModeMiddleware",
    "modules.v2.system.middleware.ModuleAccessMiddleware",
    "modules.v2.profiles.middleware.UserLastSeenMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "frontend" / "templates",
            BASE_DIR / "modules" / "plugins" / "themes",
            BASE_DIR / "modules" / "plugins" / "modules",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "social_django.context_processors.backends",
                "social_django.context_processors.login_redirect",
                "modules.v2.profiles.context_processors.user_balance",
                "modules.v2.social.context_processors.social_sidebar",
                "modules.v2.social_friends.context_processors.friends_sidebar",
                "modules.v2.notifications.context_processors.notifications_header",
                "modules.v2.system.context_processors.system_settings",
                "modules.v2.subscriptions.context_processors.subscription_context",
            ],
            "builtins": [
                "frontend.templatetags.site_formatters",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").strip().lower()

if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "caseup"),
            "USER": os.getenv("POSTGRES_USER", "caseup"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "caseup"),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Shared cache (presence throttling, Steam search, health checks). Without DJANGO_CACHE_URL Django falls
# back to per-process LocMemCache, which is not shared between gunicorn workers and Celery.
DJANGO_CACHE_URL = os.getenv("DJANGO_CACHE_URL", "").strip()
if DJANGO_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": DJANGO_CACHE_URL,
            "KEY_PREFIX": "caseup",
        }
    }

# Without an explicit LOGGING Django prints django.request errors only when DEBUG=True,
# so 500 tracebacks in production were lost. Everything goes to stderr (docker logs).
DJANGO_LOG_LEVEL = (os.getenv("DJANGO_LOG_LEVEL", "INFO").strip() or "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": DJANGO_LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": DJANGO_LOG_LEVEL, "propagate": False},
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
]
LOCALE_PATHS = [
    BASE_DIR / "locale",
]

TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "frontend" / "static",
    BASE_DIR / "modules" / "plugins" / "themes",
    BASE_DIR / "modules" / "plugins" / "modules",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
BACKUP_ROOT = BASE_DIR / os.getenv("BACKUP_DIR", "backups")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/profile/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = (
    "social_core.backends.steam.SteamOpenId",
    "django.contrib.auth.backends.ModelBackend",
)

SOCIAL_AUTH_STEAM_API_KEY = os.getenv("SOCIAL_AUTH_STEAM_API_KEY", "").strip()
SOCIAL_AUTH_STEAM_EXTRA_DATA = ["player"]
SOCIAL_AUTH_REDIRECT_IS_HTTPS = os.getenv("SOCIAL_AUTH_REDIRECT_IS_HTTPS", "0").lower() in {"1", "true", "yes"}

SOCIAL_AUTH_LOGIN_REDIRECT_URL = os.getenv("SOCIAL_AUTH_LOGIN_REDIRECT_URL", "/profile/")
SOCIAL_AUTH_NEW_USER_REDIRECT_URL = os.getenv("SOCIAL_AUTH_NEW_USER_REDIRECT_URL", "/profile/")
SOCIAL_AUTH_LOGIN_ERROR_URL = os.getenv("SOCIAL_AUTH_LOGIN_ERROR_URL", "/auth/login/")
SOCIAL_AUTH_URL_NAMESPACE = "social_auth"

FACEIT_API_KEY = os.getenv("FACEIT_API_KEY", "").strip()

SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "modules.v2.accounts.social_pipeline.steam_set_username",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.auth_allowed",
    "social_core.pipeline.social_auth.social_user",
    "modules.v2.accounts.social_pipeline.require_legal_consent",
    "modules.v2.accounts.social_pipeline.enforce_registration_enabled",
    "social_core.pipeline.user.get_username",
    "social_core.pipeline.user.create_user",
    "social_core.pipeline.social_auth.associate_user",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
    "modules.v2.accounts.social_pipeline.record_legal_consent",
)

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_TRACK_STARTED = True

GAME_SERVER_STATUS_TIMEOUT_SECONDS = float(os.getenv("GAME_SERVER_STATUS_TIMEOUT_SECONDS", "1.5"))
GAME_SERVER_STATUS_CACHE_TTL_SECONDS = int(os.getenv("GAME_SERVER_STATUS_CACHE_TTL_SECONDS", "30"))
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "1200"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "900"))
CELERY_RULE_RUNNING_STALE_SECONDS = int(os.getenv("CELERY_RULE_RUNNING_STALE_SECONDS", "3600"))
CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS = int(
    os.getenv("CELERY_BEAT_PRICE_UPDATE_SCHEDULER_SECONDS", "3600")
)

STEAM_CATALOG_GRID_COLUMNS = int(os.getenv("STEAM_CATALOG_GRID_COLUMNS", "7"))

TELEGRAM_NOTIFICATIONS_ENABLED = os.getenv("TELEGRAM_NOTIFICATIONS_ENABLED", "0").lower() in {"1", "true", "yes"}
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_WEBHOOK_MAX_BODY_BYTES = int(os.getenv("TELEGRAM_WEBHOOK_MAX_BODY_BYTES", "262144"))
WEB_CONSTRUCTOR_MAX_IMAGE_UPLOAD_BYTES = int(os.getenv("WEB_CONSTRUCTOR_MAX_IMAGE_UPLOAD_BYTES", "5242880"))
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
CASEUP_LAB_BASE_URL = os.getenv("CASEUP_LAB_BASE_URL", "").strip().rstrip("/")

CELERY_BEAT_PENDING_ALERT_DISPATCHER_SECONDS = int(
    os.getenv("CELERY_BEAT_PENDING_ALERT_DISPATCHER_SECONDS", "120")
)
CELERY_BEAT_STEAM_TRADING_UNLOCK_NOTIFIER_SECONDS = int(
    os.getenv("CELERY_BEAT_STEAM_TRADING_UNLOCK_NOTIFIER_SECONDS", "120")
)
CELERY_BEAT_CS2_STEAM_STATS_SYNC_SECONDS = int(
    os.getenv("CELERY_BEAT_CS2_STEAM_STATS_SYNC_SECONDS", "1800")  # default: 30 min
)
CELERY_BEAT_CS2_FACEIT_STATS_SYNC_SECONDS = int(
    os.getenv("CELERY_BEAT_CS2_FACEIT_STATS_SYNC_SECONDS", "1800")  # default: 30 min
)

# Balance top-up. PAYMENTS_PROVIDER: auto | yookassa | dev.
# auto = ЮKassa when YOOKASSA_SHOP_ID/YOOKASSA_SECRET_KEY are set, otherwise the fake "dev" checkout (DEBUG only).
PAYMENTS_PROVIDER = os.getenv("PAYMENTS_PROVIDER", "auto").strip().lower()
PAYMENTS_CURRENCY = os.getenv("PAYMENTS_CURRENCY", "RUB").strip().upper()
PAYMENTS_TOPUP_MIN = os.getenv("PAYMENTS_TOPUP_MIN", "50")
PAYMENTS_TOPUP_MAX = os.getenv("PAYMENTS_TOPUP_MAX", "100000")
PAYMENTS_TOPUP_PRESETS = os.getenv("PAYMENTS_TOPUP_PRESETS", "100,300,500,1000,2500")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
YOOKASSA_TIMEOUT_SECONDS = float(os.getenv("YOOKASSA_TIMEOUT_SECONDS", "15"))
YOOKASSA_SEND_RECEIPT = os.getenv("YOOKASSA_SEND_RECEIPT", "0").lower() in {"1", "true", "yes"}
YOOKASSA_VAT_CODE = int(os.getenv("YOOKASSA_VAT_CODE", "1"))
PAYMENTS_MAX_OPEN_PER_USER = int(os.getenv("PAYMENTS_MAX_OPEN_PER_USER", "5"))
# Accept provider webhooks only from the provider's addresses: auto = on unless DEBUG.
PAYMENTS_WEBHOOK_IP_CHECK = os.getenv("PAYMENTS_WEBHOOK_IP_CHECK", "auto").strip().lower()
# Reverse proxies in front of Django that append to X-Forwarded-For (prod: Nginx = 1; 0 = use REMOTE_ADDR).
PAYMENTS_WEBHOOK_PROXY_COUNT = int(os.getenv("PAYMENTS_WEBHOOK_PROXY_COUNT", "1"))
CELERY_BEAT_PAYMENTS_RECONCILE_SECONDS = int(os.getenv("CELERY_BEAT_PAYMENTS_RECONCILE_SECONDS", "300"))

# Profile → Безопасность: signed-in devices. History of ended sessions is kept this long.
USER_SESSION_RETENTION_DAYS = int(os.getenv("USER_SESSION_RETENTION_DAYS", "90"))
# City by IP for sign-ins: DB-IP City Lite (.mmdb), downloaded by the accounts.update_geoip task.
GEOIP_DATABASE_PATH = os.getenv("GEOIP_DATABASE_PATH", str(BASE_DIR / "var" / "geoip" / "dbip-city-lite.mmdb"))
# Admin+ accounts cannot use the site until they turn on two-factor authentication.
TWO_FACTOR_REQUIRED_FOR_ADMINS = os.getenv("TWO_FACTOR_REQUIRED_FOR_ADMINS", "1").strip().lower() not in {"0", "false", "no", "off"}

# Control Center → Логи: how long journal entries are kept (pruned daily by Celery beat).
ACTIVITY_LOG_RETENTION_DAYS = int(os.getenv("ACTIVITY_LOG_RETENTION_DAYS", "365"))
ACTIVITY_LOG_REQUEST_RETENTION_DAYS = int(os.getenv("ACTIVITY_LOG_REQUEST_RETENTION_DAYS", "90"))

# Secrets stored in the DB (API keys, bot tokens) are encrypted with these Fernet keys (common.encryption).
# Comma-separated: the first encrypts, all decrypt. Empty = key derived from SECRET_KEY.
FIELD_ENCRYPTION_KEYS = get_env_list("FIELD_ENCRYPTION_KEYS", "")
