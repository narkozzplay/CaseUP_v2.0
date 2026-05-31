from __future__ import annotations

from django.contrib.auth.decorators import login_required

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title


SOCIAL_PAGES = {
    "chats": {
        "key": "social_chats",
        "eyebrow": "Communication",
        "title_ru": "Чаты",
        "title_en": "Chats",
        "description_ru": "Личные чаты появятся здесь позже. Сейчас это безопасная заглушка без логики сообщений.",
        "description_en": "Private chats will live here later. This is a safe placeholder without messaging logic.",
        "empty_ru": "Чатов пока нет",
        "empty_en": "No chats yet",
    },
    "groups": {
        "key": "social_groups",
        "eyebrow": "Communication",
        "title_ru": "Группы",
        "title_en": "Groups",
        "description_ru": "Группы и сообщества будут добавлены отдельным этапом с ролями, приватностью и модерацией.",
        "description_en": "Groups and communities will be added in a later stage with roles, privacy and moderation.",
        "empty_ru": "Групп пока нет",
        "empty_en": "No groups yet",
    },
    "conversations": {
        "key": "social_conversations",
        "eyebrow": "Communication",
        "title_ru": "Беседы",
        "title_en": "Conversations",
        "description_ru": "Групповые беседы пока работают как раздел-заглушка под будущий realtime-слой.",
        "description_en": "Group conversations are currently a placeholder for the future realtime layer.",
        "empty_ru": "Бесед пока нет",
        "empty_en": "No conversations yet",
    },
}


def _render_social_page(request, page_key: str):
    page = SOCIAL_PAGES[page_key]
    title = page["title_ru"] if getattr(request, "LANGUAGE_CODE", "ru").startswith("ru") else page["title_en"]
    return render_dashboard_page(
        request,
        full_template=f"social/{page_key}.html",
        partial_template="social/partials/social_placeholder_content.html",
        context={
            "social_page": page,
            "social_active_key": page["key"],
        },
        page_title=build_page_title(title),
        active_menu_key=page["key"],
    )


@login_required
def chats_view(request):
    return _render_social_page(request, "chats")


@login_required
def groups_view(request):
    return _render_social_page(request, "groups")


@login_required
def conversations_view(request):
    return _render_social_page(request, "conversations")
