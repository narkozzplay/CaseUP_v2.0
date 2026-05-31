from __future__ import annotations

from django.contrib.auth.decorators import login_required

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .models import SocialSpace
from .services import build_space_cards


SOCIAL_PAGES = {
    "chats": {
        "key": "social_chats",
        "space_type": SocialSpace.SpaceType.DIRECT,
        "eyebrow": "Communication",
        "title_ru": "Чаты",
        "title_en": "Chats",
        "description_ru": "Личные чаты уже подключены к social spaces. Сообщения и realtime появятся отдельным этапом.",
        "description_en": "Private chats are now backed by social spaces. Messages and realtime will arrive in a later stage.",
        "empty_ru": "Чатов пока нет",
        "empty_en": "No chats yet",
    },
    "groups": {
        "key": "social_groups",
        "space_type": SocialSpace.SpaceType.GROUP,
        "eyebrow": "Communication",
        "title_ru": "Группы",
        "title_en": "Groups",
        "description_ru": "Группы получили базовую модель, роли участников и приватность. Лента и медиа остаются будущим этапом.",
        "description_en": "Groups now have the base model, member roles and privacy. Feed and media remain future work.",
        "empty_ru": "Групп пока нет",
        "empty_en": "No groups yet",
    },
    "conversations": {
        "key": "social_conversations",
        "space_type": SocialSpace.SpaceType.CONVERSATION,
        "eyebrow": "Communication",
        "title_ru": "Беседы",
        "title_en": "Conversations",
        "description_ru": "Беседы уже хранят участников и активность. Realtime-сообщения будут добавлены позже.",
        "description_en": "Conversations now store members and activity. Realtime messaging will be added later.",
        "empty_ru": "Бесед пока нет",
        "empty_en": "No conversations yet",
    },
}


def _render_social_page(request, page_key: str):
    page = SOCIAL_PAGES[page_key]
    title = page["title_ru"] if getattr(request, "LANGUAGE_CODE", "ru").startswith("ru") else page["title_en"]
    space_cards = build_space_cards(request.user, space_type=page["space_type"])
    return render_dashboard_page(
        request,
        full_template=f"social/{page_key}.html",
        partial_template="social/partials/social_placeholder_content.html",
        context={
            "social_page": page,
            "social_active_key": page["key"],
            "social_space_cards": space_cards,
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
