from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .module_registry import MODULE_REGISTRY


class SiteConfiguration(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    site_name = models.CharField(max_length=120, default="CaseUP")
    site_currency_code = models.CharField(max_length=16, default="RUB")
    site_currency_symbol = models.CharField(max_length=8, default="₽")
    support_email = models.EmailField(blank=True, default="")
    public_base_url = models.URLField(max_length=255, blank=True, default="")
    maintenance_mode_enabled = models.BooleanField(default=False)
    maintenance_message = models.TextField(blank=True, default="")
    registration_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site configuration"
        verbose_name_plural = "Site configuration"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.public_base_url = (self.public_base_url or "").strip().rstrip("/")
        self.site_currency_code = (self.site_currency_code or "RUB").strip().upper()
        self.site_currency_symbol = (self.site_currency_symbol or "₽").strip()
        self.site_name = (self.site_name or "CaseUP").strip()
        if not self.maintenance_message.strip():
            self.maintenance_message = (
                "На сайте проводятся технические работы. Попробуйте зайти позже."
            )
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.site_name or "Site configuration"

    @classmethod
    def bootstrap_defaults(cls) -> dict[str, object]:
        return {
            "site_name": "CaseUP",
            "site_currency_code": "RUB",
            "site_currency_symbol": "₽",
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
        ADMIN_PLUS = "admin_plus", "Admin+"
        SUBSCRIPTIONS_ENABLED = "subscriptions_enabled", "Subscriptions enabled"

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
                "key": "farm_legacy_logs",
                "label_ru": "Legacy weekly logs",
                "label_en": "Legacy weekly logs",
                "url_name": "farm:drops_list",
                "group": cls.Group.FARM,
                "visibility_condition": cls.Visibility.FARM_ACCESS,
                "sort_order": 40,
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
                "key": "django_admin",
                "label_ru": "Django Admin",
                "label_en": "Django Admin",
                "url_name": "admin:index",
                "group": cls.Group.ADMIN,
                "visibility_condition": cls.Visibility.ADMIN_PLUS,
                "sort_order": 70,
            },
        ]
