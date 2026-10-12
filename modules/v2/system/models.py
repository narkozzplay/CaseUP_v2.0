from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .module_registry import MODULE_REGISTRY


class ProjectVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPPORTED = "supported", "Supported"
        DEPRECATED = "deprecated", "Deprecated"
        ARCHIVED = "archived", "Archived"

    version = models.CharField(max_length=32, unique=True)
    version_order = models.PositiveIntegerField(unique=True, db_index=True)
    title = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    is_current = models.BooleanField(default=False, db_index=True)
    is_default_for_dev = models.BooleanField(default=False, db_index=True)
    is_default_for_prod = models.BooleanField(default=False, db_index=True)
    release_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("version_order", "version")
        verbose_name = "Project version"
        verbose_name_plural = "Project versions"

    def clean(self):
        duplicates = ProjectVersion.objects.exclude(pk=self.pk)
        if self.is_current and duplicates.filter(is_current=True).exists():
            raise ValidationError({"is_current": "Текущая версия проекта уже выбрана."})
        if self.is_default_for_dev and duplicates.filter(is_default_for_dev=True).exists():
            raise ValidationError({"is_default_for_dev": "Default dev-версия уже выбрана."})
        if self.is_default_for_prod and duplicates.filter(is_default_for_prod=True).exists():
            raise ValidationError({"is_default_for_prod": "Default prod-версия уже выбрана."})

    def save(self, *args, **kwargs):
        self.version = (self.version or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or self.version


class SiteConfiguration(models.Model):
    CURRENCY_RUB = "RUB"
    CURRENCY_USD = "USD"
    CURRENCY_EUR = "EUR"
    CURRENCY_GBP = "GBP"
    CURRENCY_KZT = "KZT"
    CURRENCY_BYN = "BYN"

    CURRENCY_CHOICES = (
        (CURRENCY_RUB, "Российский рубль (RUB)"),
        (CURRENCY_USD, "Доллар США (USD)"),
        (CURRENCY_EUR, "Евро (EUR)"),
        (CURRENCY_GBP, "Фунт стерлингов (GBP)"),
        (CURRENCY_KZT, "Казахстанский тенге (KZT)"),
        (CURRENCY_BYN, "Белорусский рубль (BYN)"),
    )

    CURRENCY_SYMBOLS = {
        CURRENCY_RUB: "₽",
        CURRENCY_USD: "$",
        CURRENCY_EUR: "€",
        CURRENCY_GBP: "£",
        CURRENCY_KZT: "₸",
        CURRENCY_BYN: "Br",
    }

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    site_name = models.CharField(max_length=120, default="CaseUP")
    site_currency_code = models.CharField(
        max_length=8,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_RUB,
    )
    site_currency_symbol = models.CharField(max_length=8, default="₽")
    site_currency_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text="Сколько RUB стоит 1 единица выбранной валюты.",
    )
    support_email = models.EmailField(blank=True, default="")
    public_base_url = models.URLField(max_length=255, blank=True, default="")
    maintenance_mode_enabled = models.BooleanField(default=False)
    maintenance_message = models.TextField(blank=True, default="")
    registration_enabled = models.BooleanField(default=True)
    current_project_version = models.ForeignKey(
        "ProjectVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="site_configurations",
    )
    active_theme = models.ForeignKey(
        "ThemeProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="site_configurations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site configuration"
        verbose_name_plural = "Site configuration"

    def clean(self):
        self.site_currency_code = (self.site_currency_code or self.CURRENCY_RUB).strip().upper()

        if self.site_currency_code not in dict(self.CURRENCY_CHOICES):
            raise ValidationError({"site_currency_code": "Недопустимый код валюты."})

        if self.site_currency_rate is None or self.site_currency_rate <= 0:
            raise ValidationError({"site_currency_rate": "Курс должен быть больше нуля."})

        if self.site_currency_code == self.CURRENCY_RUB:
            self.site_currency_rate = Decimal("1.000000")

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.public_base_url = (self.public_base_url or "").strip().rstrip("/")
        self.site_name = (self.site_name or "CaseUP").strip()
        self.site_currency_code = (self.site_currency_code or self.CURRENCY_RUB).strip().upper()

        self.full_clean()

        self.site_currency_symbol = self.CURRENCY_SYMBOLS.get(self.site_currency_code, "₽")

        if not (self.maintenance_message or '').strip():
            self.maintenance_message = "На сайте проводятся технические работы. Попробуйте зайти позже."

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.site_name or "Site configuration"

    @classmethod
    def bootstrap_defaults(cls) -> dict[str, object]:
        return {
            "site_name": "CaseUP",
            "site_currency_code": cls.CURRENCY_RUB,
            "site_currency_symbol": "₽",
            "site_currency_rate": Decimal("1.000000"),
            "support_email": getattr(settings, "DEFAULT_FROM_EMAIL", "") or "",
            "public_base_url": getattr(settings, "SITE_BASE_URL", "") or "",
            "maintenance_mode_enabled": False,
            "maintenance_message": "На сайте проводятся технические работы. Попробуйте зайти позже.",
            "registration_enabled": True,
        }

    @property
    def tg_notifier_enabled(self) -> bool:
        from .services import is_module_enabled

        return is_module_enabled("tg_notifier")

    @property
    def steam_catalog_enabled(self) -> bool:
        from .services import is_module_enabled

        return is_module_enabled("steam_catalog")


class ExternalServiceIntegration(models.Model):
    class HealthMode(models.TextChoices):
        AUTO = "auto", "Auto (live check)"
        FORCE_UP = "force_up", "Force UP"
        FORCE_DEGRADED = "force_degraded", "Force degraded"
        FORCE_DOWN = "force_down", "Force DOWN"

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    steam_api_key = models.CharField(max_length=255, blank=True, default="")
    faceit_api_key = models.CharField(max_length=255, blank=True, default="")
    telegram_bot_token = models.CharField(max_length=255, blank=True, default="")
    steam_health_mode = models.CharField(max_length=24, choices=HealthMode.choices, default=HealthMode.AUTO)
    faceit_health_mode = models.CharField(max_length=24, choices=HealthMode.choices, default=HealthMode.AUTO)
    telegram_health_mode = models.CharField(max_length=24, choices=HealthMode.choices, default=HealthMode.AUTO)
    degraded_stub_message = models.CharField(max_length=255, blank=True, default="Сервис временно работает с ограничениями.")
    down_stub_message = models.CharField(max_length=255, blank=True, default="Сервис временно недоступен.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "External service integration"
        verbose_name_plural = "External service integrations"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.steam_api_key = (self.steam_api_key or "").strip()
        self.faceit_api_key = (self.faceit_api_key or "").strip()
        self.telegram_bot_token = (self.telegram_bot_token or "").strip()
        self.degraded_stub_message = (self.degraded_stub_message or "").strip() or "Сервис временно работает с ограничениями."
        self.down_stub_message = (self.down_stub_message or "").strip() or "Сервис временно недоступен."
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return "External service integration"

class ModuleState(models.Model):
    key = models.CharField(max_length=64, unique=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Module state"
        verbose_name_plural = "Module states"
        ordering = ("key",)

    def clean(self):
        if self.key not in MODULE_REGISTRY:
            raise ValidationError({"key": "Неизвестный ключ модуля."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.key}: {'enabled' if self.is_enabled else 'disabled'}"

    @property
    def label(self) -> str:
        definition = MODULE_REGISTRY.get(self.key)
        return definition.label if definition else self.key


class ModuleRelease(models.Model):
    class Channel(models.TextChoices):
        STABLE = "stable", "Stable"
        TEST = "test", "Test"
        DEPRECATED = "deprecated", "Deprecated"

    module_key = models.CharField(max_length=64, db_index=True)
    version = models.CharField(max_length=32)
    version_order = models.PositiveIntegerField(default=1, db_index=True)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.STABLE, db_index=True)
    title = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Module release"
        verbose_name_plural = "Module releases"
        ordering = ("module_key", "version_order", "version")
        constraints = [
            models.UniqueConstraint(fields=("module_key", "version"), name="uniq_module_release_version"),
        ]

    def clean(self):
        if self.module_key not in MODULE_REGISTRY:
            raise ValidationError({"module_key": "Неизвестный ключ модуля."})
        duplicates = ModuleRelease.objects.exclude(pk=self.pk).filter(module_key=self.module_key)
        if self.is_default and duplicates.filter(is_default=True).exists():
            raise ValidationError({"is_default": "Для модуля уже задан default-релиз."})

    def save(self, *args, **kwargs):
        self.module_key = (self.module_key or "").strip()
        self.version = (self.version or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.module_key}@{self.version}"

    @property
    def label(self) -> str:
        definition = MODULE_REGISTRY.get(self.module_key)
        return definition.label if definition else self.module_key


class ModuleCompatibility(models.Model):
    class CompatibilityStatus(models.TextChoices):
        SUPPORTED = "supported", "Supported"
        PARTIAL = "partial", "Partial"
        UNSUPPORTED = "unsupported", "Unsupported"
        TEST_ONLY = "test_only", "Test only"

    module_release = models.ForeignKey(ModuleRelease, on_delete=models.CASCADE, related_name="compatibilities")
    project_version_from = models.ForeignKey(
        ProjectVersion,
        on_delete=models.CASCADE,
        related_name="module_compat_from",
    )
    project_version_to = models.ForeignKey(
        ProjectVersion,
        on_delete=models.CASCADE,
        related_name="module_compat_to",
    )
    compatibility_status = models.CharField(
        max_length=16,
        choices=CompatibilityStatus.choices,
        default=CompatibilityStatus.SUPPORTED,
        db_index=True,
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Module compatibility"
        verbose_name_plural = "Module compatibilities"
        constraints = [
            models.UniqueConstraint(
                fields=("module_release", "project_version_from", "project_version_to"),
                name="uniq_module_release_project_range",
            )
        ]

    def clean(self):
        if (
            self.project_version_from_id
            and self.project_version_to_id
            and self.project_version_from.version_order > self.project_version_to.version_order
        ):
            raise ValidationError({"project_version_to": "Конечная версия должна быть не ниже начальной."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.module_release} [{self.project_version_from.version} - {self.project_version_to.version}]"


class EnvironmentModuleState(models.Model):
    class Environment(models.TextChoices):
        DEV = "dev", "Dev"
        PROD = "prod", "Prod"
        TEST = "test", "Test"

    environment = models.CharField(max_length=12, choices=Environment.choices, db_index=True)
    module_key = models.CharField(max_length=64, db_index=True)
    module_release = models.ForeignKey(
        ModuleRelease,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="environment_states",
    )
    is_enabled = models.BooleanField(default=True, db_index=True)
    is_test_mode = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Environment module state"
        verbose_name_plural = "Environment module states"
        ordering = ("environment", "module_key")
        constraints = [
            models.UniqueConstraint(fields=("environment", "module_key"), name="uniq_environment_module_state"),
        ]

    def clean(self):
        if self.module_key not in MODULE_REGISTRY:
            raise ValidationError({"module_key": "Неизвестный ключ модуля."})
        if self.module_release and self.module_release.module_key != self.module_key:
            raise ValidationError({"module_release": "Релиз не соответствует ключу модуля."})
        if self.environment == self.Environment.PROD and self.is_test_mode:
            raise ValidationError({"is_test_mode": "Prod-среда не может быть test-mode."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.environment}:{self.module_key}"


class ThemeProfile(models.Model):
    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=64, blank=True, default="")
    description = models.TextField(blank=True, default="")
    template_base_path = models.CharField(max_length=255, blank=True, default="")
    css_file = models.CharField(max_length=255, blank=True, default="")
    preview_image = models.ImageField(upload_to="themes/previews/", null=True, blank=True)
    is_default = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_test_only = models.BooleanField(default=False, db_index=True)
    project_version_from = models.ForeignKey(
        ProjectVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="themes_from",
    )
    project_version_to = models.ForeignKey(
        ProjectVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="themes_to",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Theme profile"
        verbose_name_plural = "Theme profiles"
        ordering = ("name", "key")

    def clean(self):
        duplicates = ThemeProfile.objects.exclude(pk=self.pk)
        if self.is_default and duplicates.filter(is_default=True).exists():
            raise ValidationError({"is_default": "Default theme уже задан."})
        if (
            self.project_version_from_id
            and self.project_version_to_id
            and self.project_version_from.version_order > self.project_version_to.version_order
        ):
            raise ValidationError({"project_version_to": "Конечная версия темы должна быть не ниже начальной."})

    def save(self, *args, **kwargs):
        self.template_base_path = (self.template_base_path or "").strip().strip("/")
        self.css_file = (self.css_file or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class DashboardMenuItem(models.Model):
    class Group(models.TextChoices):
        OVERVIEW = "overview", "Overview"
        FARM = "farm", "Farm"
        STEAM = "steam", "Steam"
        INVESTMENTS = "investments", "Investments"
        ADMIN = "admin", "Admin panel"

    class Visibility(models.TextChoices):
        ALWAYS = "always", "Always"
        AUTHENTICATED = "authenticated", "Authenticated"
        FARM_ACCESS = "farm_access", "Farm access"
        FARM_STAFF = "farm_staff", "Farm staff"
        STEAM_CATALOG_ACCESS = "steam_catalog_access", "Steam Catalog access"
        STEAM_INVESTOR_ACCESS = "steam_investor_access", "Steam investor access"
        TG_USER_ACCESS = "tg_user_access", "TG user access"
        MODER_PLUS = "moder_plus", "Moder+"
        ADMIN_PLUS = "admin_plus", "Admin+"
        SUBSCRIPTIONS_ENABLED = "subscriptions_enabled", "Subscriptions enabled"
        STORE_ENABLED = "store_enabled", "Store enabled"

    key = models.CharField(max_length=64, unique=True)
    label_ru = models.CharField(max_length=120)
    label_en = models.CharField(max_length=120, blank=True, default="")
    url_name = models.CharField(max_length=120)
    group = models.CharField(max_length=24, choices=Group.choices, default=Group.OVERVIEW, db_index=True)
    visibility_condition = models.CharField(
        max_length=32,
        choices=Visibility.choices,
        default=Visibility.ALWAYS,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dashboard menu item"
        verbose_name_plural = "Dashboard menu items"
        ordering = ("group", "sort_order", "id")
        indexes = [
            models.Index(fields=["group", "is_enabled", "sort_order"]),
        ]

    def __str__(self) -> str:
        return self.key

    @property
    def label(self) -> str:
        return self.label_ru or self.key

    @classmethod
    def bootstrap_defaults(cls) -> list[dict[str, object]]:
        return [
            {
                "key": "home",
                "label_ru": "Главная",
                "label_en": "Home",
                "url_name": "frontend:home",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.ALWAYS,
                "sort_order": 10,
            },
            {
                "key": "dashboard_preview",
                "label_ru": "Dashboard",
                "label_en": "Dashboard",
                "url_name": "frontend:dashboard_preview",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.ALWAYS,
                "sort_order": 20,
            },
            {
                "key": "subscriptions",
                "label_ru": "Подписки",
                "label_en": "Subscriptions",
                "url_name": "subscriptions:plans",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.SUBSCRIPTIONS_ENABLED,
                "sort_order": 30,
            },
            {
                "key": "game_servers",
                "label_ru": "CS2 серверы",
                "label_en": "CS2 servers",
                "url_name": "game_servers:list",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.ALWAYS,
                "sort_order": 35,
            },
            {
                "key": "case_simulator",
                "label_ru": "Кейсы",
                "label_en": "Cases",
                "url_name": "case_simulator:list",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.AUTHENTICATED,
                "sort_order": 33,
            },
            {
                "key": "store",
                "label_ru": "Магазин",
                "label_en": "Store",
                "url_name": "store:storefront",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.STORE_ENABLED,
                "sort_order": 36,
            },
            {
                "key": "web_constructor",
                "label_ru": "Web-конструктор",
                "label_en": "Web Constructor",
                "url_name": "web_constructor:builder",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.AUTHENTICATED,
                "sort_order": 37,
            },
            {
                "key": "testing_preview",
                "label_ru": "Testing Preview",
                "label_en": "Testing Preview",
                "url_name": "frontend:testing_preview",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.ALWAYS,
                "sort_order": 40,
            },
            {
                "key": "profile",
                "label_ru": "Мой профиль",
                "label_en": "My profile",
                "url_name": "profiles:profile",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.AUTHENTICATED,
                "sort_order": 50,
            },
            {
                "key": "users_catalog",
                "label_ru": "Пользователи",
                "label_en": "Users Catalog",
                "url_name": "profiles:users_catalog",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 15,
            },
            {
                "key": "notification_center",
                "label_ru": "Центр уведомлений",
                "label_en": "Notification Center",
                "url_name": "tg_notifier:center",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.TG_USER_ACCESS,
                "sort_order": 60,
            },
            {
                "key": "farm_dashboard",
                "label_ru": "Ферма",
                "label_en": "Farm",
                "url_name": "farm:dashboard",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 10,
            },
            {
                "key": "farm_accounts",
                "label_ru": "Аккаунты",
                "label_en": "Accounts",
                "url_name": "farm:accounts_list",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 20,
            },
            {
                "key": "farm_drops",
                "label_ru": "Дропы",
                "label_en": "Drops",
                "url_name": "farm:sessions_list",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 30,
            },
            {
                "key": "tradehub_dashboard",
                "label_ru": "TradeHub",
                "label_en": "TradeHub",
                "url_name": "tradehub:dashboard",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 50,
            },
            {
                "key": "farmer_plans",
                "label_ru": "Farmer Plans",
                "label_en": "Farmer Plans",
                "url_name": "subscriptions:farmer_plans",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 60,
            },
            {
                "key": "steam_catalog",
                "label_ru": "Steam Catalog",
                "label_en": "Steam Catalog",
                "url_name": "steam_catalog:list",
                "group": cls.Group.STEAM,
                "visibility_condition": cls.Visibility.STEAM_CATALOG_ACCESS,
                "sort_order": 10,
            },
            {
                "key": "steam_investments",
                "label_ru": "Steam Investments",
                "label_en": "Steam Investments",
                "url_name": "steam_trading:investments",
                "group": cls.Group.STEAM,
                "visibility_condition": cls.Visibility.STEAM_INVESTOR_ACCESS,
                "sort_order": 20,
            },
            {
                "key": "control_center",
                "label_ru": "Control Center",
                "label_en": "Control Center",
                "url_name": "frontend:admin_panel",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 10,
            },
            {
                "key": "system_settings",
                "label_ru": "System Settings",
                "label_en": "System Settings",
                "url_name": "system_config:settings",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 20,
            },
            {
                "key": "system_versioning",
                "label_ru": "Версии и темы",
                "label_en": "Versions & Themes",
                "url_name": "system_config:versioning",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 25,
            },
            {
                "key": "celery_tasks",
                "label_ru": "Celery Tasks",
                "label_en": "Celery Tasks",
                "url_name": "celery_tasks:control_center",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 30,
            },
            {
                "key": "tg_settings",
                "label_ru": "TG Notifier",
                "label_en": "TG Notifier",
                "url_name": "tg_notifier:settings",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 40,
            },
            {
                "key": "subscription_pricing",
                "label_ru": "Subscription Pricing",
                "label_en": "Subscription Pricing",
                "url_name": "subscriptions:pricing_dashboard",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 50,
            },
            {
                "key": "farm_settings",
                "label_ru": "Farm Settings",
                "label_en": "Farm Settings",
                "url_name": "farm:settings",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 60,
            },
            {
                "key": "web_constructor_moderation",
                "label_ru": "Модерация конструктора",
                "label_en": "Constructor moderation",
                "url_name": "web_constructor:moderation",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 65,
            },
            {
                "key": "cs2_stats",
                "label_ru": "CS2 Статистика",
                "label_en": "CS2 Stats",
                "url_name": "cs2_stats:own",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.AUTHENTICATED,
                "sort_order": 55,
            },
            {
                "key": "cs2_match_history",
                "label_ru": "CS2 История матчей",
                "label_en": "CS2 Match History",
                "url_name": "cs2_stats:match_history",
                "group": cls.Group.OVERVIEW,
                "visibility_condition": cls.Visibility.AUTHENTICATED,
                "sort_order": 56,
            },
            {
                "key": "verification_requests",
                "label_ru": "Заявки на верификацию",
                "label_en": "Verification Requests",
                "url_name": "profiles:verification_requests",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.MODER_PLUS,
                "sort_order": 16,
            },
            {
                "key": "customization_catalog",
                "label_ru": "Каталог кастомизации",
                "label_en": "Customization Catalog",
                "url_name": "profile_customization:catalog",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 67,
            },
            {
                "key": "django_admin",
                "label_ru": "Django Admin",
                "label_en": "Django Admin",
                "url_name": "admin:index",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 70,
            },
        ]
