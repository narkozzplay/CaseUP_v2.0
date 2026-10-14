from __future__ import annotations

from modules.v2.web_constructor.models import TemplateVariable


VARIABLE_SPECS = [
    {"key": "site.name", "label": "Site name", "group": "system", "source": "modules.v2.system.context_processors.system_settings", "value_type": TemplateVariable.ValueType.STRING, "example_value": "CaseUP", "resolver_hint": "site_settings.site_name", "description": "Имя проекта из SiteConfiguration."},
    {"key": "site.support_email", "label": "Support email", "group": "system", "source": "modules.v2.system.context_processors.system_settings", "value_type": TemplateVariable.ValueType.STRING, "example_value": "support@example.com", "resolver_hint": "site_settings.support_email", "description": "Контакт поддержки из SiteConfiguration."},
    {"key": "theme.key", "label": "Active theme key", "group": "system", "source": "modules.v2.system.context_processors.system_settings", "value_type": TemplateVariable.ValueType.STRING, "example_value": "default", "resolver_hint": "active_theme.key", "description": "Активная тема проекта."},
    {"key": "user.username", "label": "Username", "group": "profile", "source": "django.contrib.auth.context_processors.auth", "value_type": TemplateVariable.ValueType.STRING, "example_value": "builder", "resolver_hint": "request.user.username", "description": "Имя текущего пользователя."},
    {"key": "user.balance", "label": "User balance", "group": "profile", "source": "modules.v2.profiles.context_processors.user_balance", "value_type": TemplateVariable.ValueType.NUMBER, "example_value": "2500", "resolver_hint": "user_balance", "description": "Баланс пользователя из профиля."},
    {"key": "user.avatar", "label": "User avatar", "group": "profile", "source": "modules.v2.profiles.models.UserProfile", "value_type": TemplateVariable.ValueType.IMAGE, "example_value": "https://cdn.example.com/avatar.png", "resolver_hint": "request.user.profile_v2.avatar_url", "description": "Аватар пользователя, если доступен."},
    {"key": "subscription.plan", "label": "Subscription plan", "group": "subscriptions", "source": "modules.v2.subscriptions.context_processors.subscription_context", "value_type": TemplateVariable.ValueType.STRING, "example_value": "Farmer Pro", "resolver_hint": "subscription_context.active_plan", "description": "Активный тариф пользователя."},
    {"key": "catalog.items_total", "label": "Catalog items total", "group": "steam_catalog", "source": "modules.v2.steam_catalog.models.SteamItem", "value_type": TemplateVariable.ValueType.NUMBER, "example_value": "1024", "resolver_hint": "steam_catalog total", "description": "Количество элементов каталога для summary-блоков."},
]


def sync_template_variables() -> list[TemplateVariable]:
    variables = []
    for spec in VARIABLE_SPECS:
        variable, _created = TemplateVariable.objects.update_or_create(
            key=spec["key"],
            defaults={k: v for k, v in spec.items() if k != "key"},
        )
        variables.append(variable)
    return variables


def search_template_variables(term: str = ""):
    qs = TemplateVariable.objects.filter(is_active=True).order_by("group", "label", "key")
    term = (term or "").strip()
    if term:
        qs = qs.filter(key__icontains=term) | qs.filter(label__icontains=term) | qs.filter(description__icontains=term)
    return qs.distinct()


def serialize_template_variable(variable: TemplateVariable) -> dict:
    return {
        "id": variable.id,
        "key": variable.key,
        "label": variable.label,
        "group": variable.group,
        "source": variable.source,
        "value_type": variable.value_type,
        "example_value": variable.example_value,
        "resolver_hint": variable.resolver_hint,
        "description": variable.description,
        "allowed_pages": list(variable.allowed_pages.values_list("route_key", flat=True)),
    }
