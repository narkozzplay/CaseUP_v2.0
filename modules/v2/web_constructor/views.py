from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_lab_url, build_page_title, build_site_url, request_is_lab_host

from .forms import PageTemplateCreateForm, PageTemplateEditorForm, TemplateAssetUploadForm, TemplateModerationForm
from .models import EditablePage, PageTemplate, PageTemplateVersion, SandboxSession, TemplateAsset, TemplateVariable
from .services import (
    TemplateSchemaError,
    TemplateWorkflowError,
    approve_template_version,
    clone_page_template,
    create_page_template,
    get_or_create_editable_version,
    render_preview_fragment,
    rollback_template_to_version,
    save_template_draft,
    submit_template_version,
    reject_template_version,
    build_builder_state,
    ensure_style_presets,
    get_or_create_sandbox_session,
    reset_sandbox_page,
    save_sandbox_draft,
    search_template_variables,
    serialize_template_variable,
    sync_editable_pages,
    sync_template_variables,
)


def _can_admin(request) -> bool:
    access = get_user_role(request.user)
    return access.is_admin_plus or access.is_moder_plus


def _get_template_for_actor(request, template_uuid, *, allow_staff_preview: bool = True) -> PageTemplate:
    template = get_object_or_404(
        PageTemplate.objects.select_related("owner", "current_version", "approved_version"),
        template_uuid=template_uuid,
    )
    role_access = get_user_role(request.user)
    if getattr(request.user, "is_authenticated", False) and (template.owner_id == request.user.id or role_access.is_admin_plus):
        return template
    if allow_staff_preview and getattr(request.user, "is_authenticated", False) and role_access.is_moder_plus:
        return template
    raise Http404("Шаблон не найден.")


def _build_dashboard_context(request) -> dict:
    role_access = get_user_role(request.user)
    my_templates = PageTemplate.objects.filter(owner=request.user).select_related("current_version", "approved_version").order_by("-updated_at", "-id")
    pending_versions = PageTemplateVersion.objects.select_related("template", "template__owner").filter(moderation_status=PageTemplateVersion.ModerationStatus.PENDING).order_by("submitted_at", "-id")
    return {
        "role_access": role_access,
        "templates": my_templates,
        "templates_total": my_templates.count(),
        "approved_total": my_templates.filter(approved_version__isnull=False).count(),
        "pending_total": my_templates.filter(status=PageTemplate.Status.PENDING_MODERATION).count(),
        "moderation_total": pending_versions.count() if role_access.is_moder_plus else 0,
        "recent_pending_versions": pending_versions[:8] if role_access.is_moder_plus else [],
    }


@login_required
def dashboard_page(request):
    context = _build_dashboard_context(request)
    return render_dashboard_page(
        request,
        full_template="web_constructor/dashboard.html",
        partial_template="web_constructor/partials/dashboard_content.html",
        context=context,
        page_title=build_page_title("Web Constructor"),
        active_menu_key="web_constructor",
        current_url=reverse("web_constructor:dashboard"),
    )


@login_required
def create_template_page(request):
    if request.method == "POST":
        form = PageTemplateCreateForm(request.POST)
        if form.is_valid():
            template = create_page_template(owner=request.user, cleaned_data=form.cleaned_data)
            messages.success(request, "Шаблон создан. Теперь можно редактировать блоки и отправлять версию на модерацию.")
            return redirect("web_constructor:editor", template_uuid=template.template_uuid)
    else:
        form = PageTemplateCreateForm()
    context = {"role_access": get_user_role(request.user), "form": form}
    return render_dashboard_page(
        request,
        full_template="web_constructor/create.html",
        partial_template="web_constructor/partials/create_content.html",
        context=context,
        page_title=build_page_title("Create template"),
        active_menu_key="web_constructor",
        current_url=reverse("web_constructor:create"),
    )


@login_required
def template_detail_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    versions = template.versions.select_related("approved_by").all()[:12]
    ownerships = template.ownerships.select_related("user", "active_version").all()
    context = {
        "role_access": get_user_role(request.user),
        "template_obj": template,
        "versions": versions,
        "ownerships": ownerships,
        "public_render_url": reverse("web_constructor:render", kwargs={"template_uuid": template.template_uuid}),
    }
    return render_dashboard_page(
        request,
        full_template="web_constructor/detail.html",
        partial_template="web_constructor/partials/detail_content.html",
        context=context,
        page_title=build_page_title(f"Template · {template.title}"),
        active_menu_key="web_constructor",
        current_url=reverse("web_constructor:detail", kwargs={"template_uuid": template.template_uuid}),
    )


@login_required
def editor_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    if template.owner_id != request.user.id and not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Редактировать можно только свои шаблоны.")
    version = get_or_create_editable_version(template)
    if request.method == "POST":
        form = PageTemplateEditorForm(request.POST, request.FILES, instance=template, initial_schema=version.schema_json)
        if form.is_valid():
            version = save_template_draft(template=template, version=version, cleaned_data=form.cleaned_data)
            action = request.POST.get("editor_action") or "save"
            if action == "submit":
                submit_template_version(template=template, version=version)
                messages.success(request, f"Версия v{version.version_number} отправлена на модерацию.")
                return redirect("web_constructor:detail", template_uuid=template.template_uuid)
            messages.success(request, f"Черновик v{version.version_number} сохранён.")
            return redirect("web_constructor:editor", template_uuid=template.template_uuid)
    else:
        form = PageTemplateEditorForm(instance=template, initial_schema=version.schema_json, initial={"changelog": version.changelog})
    assets_queryset = TemplateAsset.objects.filter(owner=template.owner)
    if template.pk:
        assets_queryset = assets_queryset.filter(Q(template__isnull=True) | Q(template=template))
    else:
        assets_queryset = assets_queryset.filter(template__isnull=True)
    assets = list(assets_queryset.order_by("-created_at", "-id"))
    asset_payload = [
        {"id": asset.id, "title": asset.title or asset.alt_text or asset.file.name, "url": asset.file.url if asset.file else "", "type": asset.asset_type}
        for asset in assets
    ]
    preview_html = version.rendered_preview_cache or render_preview_fragment(template=template, version=version)[0]
    context = {
        "role_access": get_user_role(request.user),
        "template_obj": template,
        "editable_version": version,
        "form": form,
        "asset_form": TemplateAssetUploadForm(),
        "assets": assets,
        "asset_payload": asset_payload,
        "preview_html": preview_html,
        "editor_manifest": form.editor_manifest,
        "versions": template.versions.select_related("approved_by").all()[:8],
        "public_render_url": reverse("web_constructor:render", kwargs={"template_uuid": template.template_uuid}),
    }
    return render_dashboard_page(
        request,
        full_template="web_constructor/editor.html",
        partial_template="web_constructor/partials/editor_content.html",
        context=context,
        page_title=build_page_title(f"Constructor Editor · {template.title}"),
        active_menu_key="web_constructor",
        current_url=reverse("web_constructor:editor", kwargs={"template_uuid": template.template_uuid}),
    )


@login_required
@require_POST
def upload_asset_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    if template.owner_id != request.user.id and not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Asset library доступна только владельцу шаблона.")
    form = TemplateAssetUploadForm(request.POST, request.FILES)
    if form.is_valid():
        asset = form.save(commit=False)
        asset.owner = template.owner
        asset.template = template
        asset.save()
        messages.success(request, f"Asset '{asset.title or asset.file.name}' загружен.")
    else:
        messages.error(request, "Не удалось загрузить asset. Проверь файл и метаданные.")
    return redirect("web_constructor:editor", template_uuid=template.template_uuid)


@login_required
def version_history_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    context = {
        "role_access": get_user_role(request.user),
        "template_obj": template,
        "versions": template.versions.select_related("approved_by").all(),
    }
    return render_dashboard_page(
        request,
        full_template="web_constructor/version_history.html",
        partial_template="web_constructor/partials/version_history_content.html",
        context=context,
        page_title=build_page_title(f"Versions · {template.title}"),
        active_menu_key="web_constructor",
        current_url=reverse("web_constructor:versions", kwargs={"template_uuid": template.template_uuid}),
    )


@login_required
@require_POST
def clone_template_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    cloned = clone_page_template(template=template, owner=request.user)
    messages.success(request, f"Шаблон '{template.title}' клонирован в новый draft.")
    return redirect("web_constructor:editor", template_uuid=cloned.template_uuid)


@login_required
@require_POST
def rollback_template_page(request, template_uuid, version_id):
    template = _get_template_for_actor(request, template_uuid)
    if template.owner_id != request.user.id and not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Откат доступен только владельцу или admin+.")
    version = get_object_or_404(PageTemplateVersion, pk=version_id, template=template)
    try:
        rollback_template_to_version(template=template, version=version)
    except TemplateWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Live-версия шаблона переключена на v{version.version_number}.")
    return redirect("web_constructor:versions", template_uuid=template.template_uuid)


@login_required
def moderation_queue_page(request):
    if not _can_admin(request):
        return HttpResponseForbidden("Очередь модерации доступна только moderator+.")
    pending_versions = PageTemplateVersion.objects.select_related("template", "template__owner").filter(
        moderation_status=PageTemplateVersion.ModerationStatus.PENDING
    ).order_by("submitted_at", "-id")
    context = {
        "role_access": get_user_role(request.user),
        "pending_versions": pending_versions,
        "moderation_form": TemplateModerationForm(),
    }
    return render_dashboard_page(
        request,
        full_template="web_constructor/moderation_queue.html",
        partial_template="web_constructor/partials/moderation_queue_content.html",
        context=context,
        page_title=build_page_title("Constructor moderation"),
        active_menu_key="web_constructor_moderation",
        current_url=reverse("web_constructor:moderation"),
    )


@login_required
@require_POST
def moderate_version_page(request, version_id):
    if not _can_admin(request):
        return HttpResponseForbidden("Модерация доступна только moderator+.")
    version = get_object_or_404(PageTemplateVersion.objects.select_related("template", "template__owner"), pk=version_id)
    form = TemplateModerationForm(request.POST)
    comment = ""
    if form.is_valid():
        comment = form.cleaned_data.get("moderation_comment") or ""
    action = request.POST.get("moderation_action")
    if action == "approve":
        approve_template_version(version=version, moderator=request.user, comment=comment)
        messages.success(request, f"Версия v{version.version_number} одобрена.")
    elif action == "reject":
        reject_template_version(version=version, comment=comment)
        messages.warning(request, f"Версия v{version.version_number} отклонена.")
    else:
        messages.error(request, "Неизвестное действие модерации.")
    return redirect("web_constructor:moderation")


@login_required
@require_POST
def preview_fragment_page(request, template_uuid):
    template = _get_template_for_actor(request, template_uuid)
    schema_raw = request.POST.get("schema_json") or "{}"
    try:
        schema = json.loads(schema_raw)
        version = template.current_version or template.approved_version
        html, payload = render_preview_fragment(template=template, version=version, schema_override=schema)
    except (json.JSONDecodeError, TemplateSchemaError) as exc:
        return HttpResponseBadRequest(str(exc))
    response = HttpResponse(html)
    response["X-Constructor-External-Links"] = str(int(bool(payload["external_links"])))
    response["X-Constructor-External-Links-Count"] = str(len(payload["external_links"]))
    return response



def _resolve_builder_preview_url(page: EditablePage) -> str:
    if page.is_dynamic:
        return ""
    return page.route_path


def _redirect_builder_pages_to_lab(request):
    lab_target = build_lab_url(request.get_full_path())
    if not lab_target or request_is_lab_host(request):
        return None
    return redirect(lab_target)


@login_required
def builder_dashboard_page(request):
    lab_redirect = _redirect_builder_pages_to_lab(request)
    if lab_redirect is not None:
        return lab_redirect
    sync_editable_pages()
    sync_template_variables()
    ensure_style_presets()
    selected_page = EditablePage.objects.filter(is_active=True).order_by("sort_order", "route_path", "id").first()
    if selected_page is None:
        raise Http404("Editable pages registry is empty.")
    return redirect("web_constructor:builder_editor", page_id=selected_page.id)


@login_required
def builder_editor_page(request, page_id: int):
    lab_redirect = _redirect_builder_pages_to_lab(request)
    if lab_redirect is not None:
        return lab_redirect
    sync_editable_pages()
    sync_template_variables()
    ensure_style_presets()
    selected_page = get_object_or_404(EditablePage.objects.prefetch_related("areas"), pk=page_id, is_active=True)
    session = get_or_create_sandbox_session(owner=request.user, editable_page=selected_page)
    pages = list(EditablePage.objects.filter(is_active=True).order_by("sort_order", "route_path", "id"))
    preview_url = _resolve_builder_preview_url(selected_page)
    builder_state = build_builder_state(session=session, editable_page=selected_page)
    context = {
        "role_access": get_user_role(request.user),
        "builder_pages": pages,
        "selected_page": selected_page,
        "sandbox_session": session,
        "builder_state": builder_state,
        "preview_url": preview_url,
        "preview_open_url": build_site_url(selected_page.route_path),
        "site_dashboard_url": build_site_url(reverse("frontend:dashboard_preview")),
        "legacy_constructor_url": build_lab_url(reverse("web_constructor:dashboard")) or reverse("web_constructor:dashboard"),
        "variables_total": TemplateVariable.objects.filter(is_active=True).count(),
        "builder_page_title": build_page_title(f"Builder · {selected_page.title}"),
    }
    return render(request, "web_constructor/builder.html", context)


@login_required
def builder_variables_page(request):
    sync_template_variables()
    term = request.GET.get("q", "")
    variables = [serialize_template_variable(item) for item in search_template_variables(term)]
    return JsonResponse({"items": variables, "query": term, "total": len(variables)})


@login_required
@require_POST
def builder_save_draft_page(request, page_id: int):
    selected_page = get_object_or_404(EditablePage, pk=page_id, is_active=True)
    session = get_or_create_sandbox_session(owner=request.user, editable_page=selected_page)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON payload.")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("Draft payload must be an object.")
    session = save_sandbox_draft(session=session, editable_page=selected_page, payload=payload)
    return JsonResponse({
        "ok": True,
        "session_id": session.id,
        "saved_at": session.last_saved_at.isoformat() if session.last_saved_at else None,
        "page_key": selected_page.route_key,
    })


@login_required
@require_POST
def builder_reset_page(request, page_id: int):
    selected_page = get_object_or_404(EditablePage, pk=page_id, is_active=True)
    session = get_or_create_sandbox_session(owner=request.user, editable_page=selected_page)
    session = reset_sandbox_page(session=session, editable_page=selected_page)
    return JsonResponse({"ok": True, "session_id": session.id, "page_key": selected_page.route_key})


@login_required
def builder_state_page(request, page_id: int):
    selected_page = get_object_or_404(EditablePage.objects.prefetch_related("areas"), pk=page_id, is_active=True)
    session = get_or_create_sandbox_session(owner=request.user, editable_page=selected_page)
    return JsonResponse(build_builder_state(session=session, editable_page=selected_page))

def render_template_page(request, template_uuid):
    template = get_object_or_404(PageTemplate.objects.select_related("owner", "current_version", "approved_version"), template_uuid=template_uuid)
    role_access = get_user_role(request.user)
    is_owner_or_admin = bool(getattr(request.user, "is_authenticated", False) and (request.user.id == template.owner_id or role_access.is_admin_plus))
    if is_owner_or_admin:
        version = template.current_version or template.approved_version
    else:
        if not template.is_active or template.visibility == PageTemplate.Visibility.PRIVATE:
            raise Http404("Шаблон скрыт.")
        version = template.approved_version
        if version is None or version.moderation_status != PageTemplateVersion.ModerationStatus.APPROVED:
            raise Http404("Approved-версия шаблона не найдена.")
    if version is None:
        raise Http404("У шаблона нет версии для рендера.")
    payload = render_preview_fragment(template=template, version=version)[1]
    return render(
        request,
        "web_constructor/render/page_public.html",
        {
            "role_access": role_access,
            **payload,
            "template_obj": template,
            "version": version,
            "is_owner_or_admin": is_owner_or_admin,
        },
    )
