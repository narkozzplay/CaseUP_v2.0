from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import SocialConversationCreateForm, SocialGroupCreateForm, SocialMessageCreateForm
from .models import SocialSpace
from .services import (
    build_space_cards,
    build_space_detail_context,
    build_space_url,
    create_social_space,
    create_space_message,
    get_user_space_or_404,
    mark_space_read,
)


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
        "create_url_name": "social:group_create",
        "create_label_ru": "Создать группу",
        "create_label_en": "Create group",
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
        "create_url_name": "social:conversation_create",
        "create_label_ru": "Создать беседу",
        "create_label_en": "Create conversation",
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


def _render_create_page(request, *, form, page: dict[str, object], active_menu_key: str):
    title = page["title_ru"] if getattr(request, "LANGUAGE_CODE", "ru").startswith("ru") else page["title_en"]
    return render_dashboard_page(
        request,
        full_template=page["full_template"],
        partial_template="social/partials/space_create_content.html",
        context={
            "form": form,
            "create_page": page,
        },
        page_title=build_page_title(title),
        active_menu_key=active_menu_key,
    )


@login_required
def group_create_view(request):
    page = {
        "eyebrow": "Communication",
        "title_ru": "Создать группу",
        "title_en": "Create group",
        "description_ru": "Создайте базовое social space для группы. Лента, медиа и инвайты появятся позже.",
        "description_en": "Create a base social space for a group. Feed, media and invites will come later.",
        "submit_ru": "Создать группу",
        "submit_en": "Create group",
        "back_url": "social:groups",
        "full_template": "social/group_create.html",
    }
    if request.method == "POST":
        form = SocialGroupCreateForm(request.POST, current_user=request.user)
        if form.is_valid():
            space = create_social_space(
                request.user,
                form.cleaned_data["title"],
                SocialSpace.SpaceType.GROUP,
                privacy=form.cleaned_data["privacy"],
                description=form.cleaned_data.get("description") or "",
                members=form.cleaned_data.get("members"),
            )
            return redirect(build_space_url(space))
        return _render_create_page(request, form=form, page=page, active_menu_key="social_groups")

    form = SocialGroupCreateForm(current_user=request.user)
    return _render_create_page(request, form=form, page=page, active_menu_key="social_groups")


@login_required
def conversation_create_view(request):
    page = {
        "eyebrow": "Communication",
        "title_ru": "Создать беседу",
        "title_en": "Create conversation",
        "description_ru": "Создайте беседу с участниками. Сообщения будут добавлены следующим этапом.",
        "description_en": "Create a conversation with members. Messages will be added in the next stage.",
        "submit_ru": "Создать беседу",
        "submit_en": "Create conversation",
        "back_url": "social:conversations",
        "full_template": "social/conversation_create.html",
    }
    if request.method == "POST":
        form = SocialConversationCreateForm(request.POST, current_user=request.user)
        if form.is_valid():
            space = create_social_space(
                request.user,
                form.cleaned_data["title"],
                SocialSpace.SpaceType.CONVERSATION,
                privacy=form.cleaned_data["privacy"],
                description=form.cleaned_data.get("description") or "",
                members=form.cleaned_data.get("members"),
            )
            return redirect(build_space_url(space))
        return _render_create_page(request, form=form, page=page, active_menu_key="social_conversations")

    form = SocialConversationCreateForm(current_user=request.user)
    return _render_create_page(request, form=form, page=page, active_menu_key="social_conversations")


@login_required
def space_detail_view(request, space_id: int, slug: str):
    space = get_user_space_or_404(request.user, space_id=space_id, slug=slug)
    canonical_url = build_space_url(space)
    if slug != space.slug:
        return redirect(canonical_url)

    mark_space_read(request.user, space)
    return _render_space_detail(request, space)


def _render_space_detail(request, space: SocialSpace, *, message_form=None):
    context = build_space_detail_context(request.user, space)
    context["message_form"] = message_form or SocialMessageCreateForm()
    return render_dashboard_page(
        request,
        full_template="social/space_detail.html",
        partial_template="social/partials/space_detail_content.html",
        context=context,
        page_title=build_page_title(space.title),
        active_menu_key=context["space_active_menu_key"],
    )


@login_required
def space_message_create_view(request, space_id: int, slug: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    space = get_user_space_or_404(request.user, space_id=space_id, slug=slug)
    canonical_url = build_space_url(space)
    if slug != space.slug:
        return redirect(canonical_url)

    form = SocialMessageCreateForm(request.POST)
    if form.is_valid():
        create_space_message(request.user, space, form.cleaned_data["body"])
        return redirect(canonical_url)

    return _render_space_detail(request, space, message_form=form)
