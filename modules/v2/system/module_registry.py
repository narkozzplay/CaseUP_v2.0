from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDefinition:
    key: str
    label: str
    description: str
    namespaces: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    addon_targets: tuple[str, ...] = ()
    default_enabled: bool = True
    # Admins and above may use the module even while it is switched off for everyone else.
    admin_open: bool = False


MODULE_REGISTRY: dict[str, ModuleDefinition] = {
    "steam_catalog": ModuleDefinition(
        key="steam_catalog",
        label="Steam Catalog",
        description="Каталог Steam-предметов и связанные операции.",
        namespaces=("steam_catalog",),
        addon_targets=("subscriptions",),
        default_enabled=True,
    ),
    "market": ModuleDefinition(
        key="market",
        label="Рынок",
        description="P2P-торговля предметами Steam между игроками с гарантом сделки.",
        namespaces=("market",),
        default_enabled=False,
        admin_open=True,
    ),
    "farm": ModuleDefinition(
        key="farm",
        label="Farm",
        description="Операционный модуль фарм-аккаунтов.",
        namespaces=("farm",),
        default_enabled=True,
    ),
    "tradehub": ModuleDefinition(
        key="tradehub",
        label="TradeHub",
        description="Управление торговыми ботами и продажами.",
        namespaces=("tradehub",),
        default_enabled=True,
    ),
    "steam_trading": ModuleDefinition(
        key="steam_trading",
        label="Steam Trading",
        description="Учет закупок, холда и продаж Steam-предметов.",
        namespaces=("steam_trading",),
        dependencies=("steam_catalog",),
        addon_targets=("subscriptions",),
        default_enabled=True,
    ),
    "celery_tasks": ModuleDefinition(
        key="celery_tasks",
        label="Celery Tasks",
        description="Панель расписаний фоновых задач.",
        namespaces=("celery_tasks",),
        default_enabled=True,
    ),
    "tg_notifier": ModuleDefinition(
        key="tg_notifier",
        label="TG Notifier",
        description="Телеграм-уведомления и webhook-интеграция.",
        namespaces=("tg_notifier", "tg_notifier_public"),
        addon_targets=("subscriptions",),
        default_enabled=False,
    ),
    "subscriptions": ModuleDefinition(
        key="subscriptions",
        label="Subscriptions",
        description="Коммерческие тарифы, feature-based доступ и paywall-состояния.",
        namespaces=("subscriptions",),
        default_enabled=True,
    ),
    "game_servers": ModuleDefinition(
        key="game_servers",
        label="Game Servers",
        description="Локальная интеграция игровых серверов, мониторинг статуса и foundation под привилегии.",
        namespaces=("game_servers",),
        addon_targets=("subscriptions",),
        default_enabled=True,
    ),
    "case_simulator": ModuleDefinition(
        key="case_simulator",
        label="Case Simulator",
        description="Симуляция открытия кейсов CS2 и своих кейсов CaseUP за внутренний баланс, терминал с предложениями.",
        namespaces=("case_simulator",),
        default_enabled=True,
    ),
    "store": ModuleDefinition(
        key="store",
        label="Store",
        description="Foundation-модуль storefront/витрины (пока выключен по умолчанию).",
        namespaces=("store",),
        addon_targets=("subscriptions",),
        default_enabled=False,
    ),
    "cs2_stats": ModuleDefinition(
        key="cs2_stats",
        label="CS2 Stats",
        description="Статистика CS2 из Steam API и FACEIT: K/D, HS%, per-map, сравнение.",
        namespaces=("cs2_stats",),
        default_enabled=True,
    ),
    "profile_customization": ModuleDefinition(
        key="profile_customization",
        label="Profile Customization",
        description="Каталог анимированных фонов и аватарок Steam для кастомизации профиля.",
        namespaces=("profile_customization",),
        default_enabled=True,
    ),
    "majors": ModuleDefinition(
        key="majors",
        label="Мейджор и Pick’em",
        description="Страница мейджора CS2, прогнозы на стадии (Pick'em), таблица лидеров и награды.",
        namespaces=("majors",),
        default_enabled=True,
    ),
    "support": ModuleDefinition(
        key="support",
        label="Поддержка",
        description="Служба поддержки: обращения пользователей и очередь тикетов для модераторов.",
        namespaces=("support",),
        default_enabled=True,
    ),
}

NAMESPACE_TO_MODULE_KEY = {
    namespace: module.key
    for module in MODULE_REGISTRY.values()
    for namespace in module.namespaces
}
