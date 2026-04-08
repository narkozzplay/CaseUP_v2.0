from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from modules.v2.system.services import get_site_configuration
from django.utils import timezone
from django.utils.text import slugify

from ..models import PageTemplate, PageTemplateVersion, TemplateAsset, UserTemplateOwnership
from .renderer import render_preview_fragment
from .schema import build_default_schema, normalize_schema


class TemplateWorkflowError(ValueError):
    pass


def _sync_template_status(template: PageTemplate) -> PageTemplate:
    if template.status == PageTemplate.Status.ARCHIVED:
        return template
    if template.current_version and template.current_version.moderation_status == PageTemplateVersion.ModerationStatus.PENDING:
        template.status = PageTemplate.Status.PENDING_MODERATION
    elif template.approved_version_id:
        template.status = PageTemplate.Status.APPROVED
    elif template.current_version and template.current_version.moderation_status == PageTemplateVersion.ModerationStatus.REJECTED:
        template.status = PageTemplate.Status.REJECTED
    else:
        template.status = PageTemplate.Status.DRAFT
    return template


def _generate_unique_slug(*, base_title: str, slug_candidate: str = "", exclude_template_id: int | None = None) -> str:
    base = slugify(slug_candidate or base_title or "template")[:150] or "template"
    slug = base
    idx = 2
    qs = PageTemplate.objects.all()
    if exclude_template_id:
        qs = qs.exclude(pk=exclude_template_id)
    while qs.filter(slug=slug).exists():
        suffix = f"-{idx}"
        slug = f"{base[:160 - len(suffix)]}{suffix}"
        idx += 1
    return slug


def _next_version_number(template: PageTemplate) -> int:
    last = template.versions.order_by("-version_number").first()
    return (last.version_number if last else 0) + 1


def get_or_create_editable_version(template: PageTemplate) -> PageTemplateVersion:
    current = template.current_version
    if current and current.moderation_status in {PageTemplateVersion.ModerationStatus.DRAFT, PageTemplateVersion.ModerationStatus.REJECTED}:
        return current
    source = template.current_version or template.approved_version
    base_schema = source.schema_json if source else build_default_schema(template.title)
    html, payload = render_preview_fragment(template=template, version=source, schema_override=base_schema)
    version = PageTemplateVersion.objects.create(
        template=template,
        version_number=_next_version_number(template),
        schema_json=base_schema,
        compiled_snapshot={"page": payload["page"], "blocks": payload["blocks"]},
        rendered_preview_cache=html,
        moderation_status=PageTemplateVersion.ModerationStatus.DRAFT,
        contains_external_links=bool(payload["external_links"]),
        external_links_snapshot=payload["external_links"],
    )
    template.current_version = version
    _sync_template_status(template)
    template.save(update_fields=["current_version", "status", "updated_at"])
    UserTemplateOwnership.objects.filter(user=template.owner, template=template).update(active_version=version)
    return version


@transaction.atomic
def create_page_template(*, owner, cleaned_data: dict) -> PageTemplate:
    template = PageTemplate.objects.create(
        owner=owner,
        title=cleaned_data["title"],
        slug=_generate_unique_slug(base_title=cleaned_data["title"], slug_candidate=cleaned_data.get("slug") or ""),
        description=cleaned_data.get("description", ""),
        page_type=cleaned_data.get("page_type") or PageTemplate.PageType.LANDING,
        visibility=cleaned_data.get("visibility") or PageTemplate.Visibility.PRIVATE,
        access_mode=cleaned_data.get("access_mode") or PageTemplate.AccessMode.PERSONAL,
        is_free=bool(cleaned_data.get("is_free", True)),
        price=cleaned_data.get("price") or Decimal("0.00"),
        currency=cleaned_data.get("currency") or PageTemplate._meta.get_field("currency").default,
    )
    schema = build_default_schema(template.title)
    html, payload = render_preview_fragment(template=template, schema_override=schema)
    version = PageTemplateVersion.objects.create(
        template=template,
        version_number=1,
        schema_json=schema,
        compiled_snapshot={"page": payload["page"], "blocks": payload["blocks"]},
        rendered_preview_cache=html,
        moderation_status=PageTemplateVersion.ModerationStatus.DRAFT,
        contains_external_links=bool(payload["external_links"]),
        external_links_snapshot=payload["external_links"],
    )
    template.current_version = version
    _sync_template_status(template)
    template.save(update_fields=["current_version", "status", "updated_at"])
    UserTemplateOwnership.objects.create(
        user=owner,
        template=template,
        ownership_type=UserTemplateOwnership.OwnershipType.OWNER,
        active_version=version,
    )
    return template


@transaction.atomic
def save_template_draft(*, template: PageTemplate, version: PageTemplateVersion, cleaned_data: dict) -> PageTemplateVersion:
    template.title = cleaned_data["title"]
    template.slug = _generate_unique_slug(base_title=template.title, slug_candidate=cleaned_data.get("slug") or "", exclude_template_id=template.id)
    template.description = cleaned_data.get("description") or ""
    template.page_type = cleaned_data.get("page_type") or template.page_type
    template.visibility = cleaned_data.get("visibility") or template.visibility
    template.access_mode = cleaned_data.get("access_mode") or template.access_mode
    template.is_free = bool(cleaned_data.get("is_free", template.is_free))
    template.price = cleaned_data.get("price") or Decimal("0.00")
    template.currency = cleaned_data.get("currency") or template.currency
    if cleaned_data.get("cover_image") is not None:
        template.cover_image = cleaned_data["cover_image"]
    if version.moderation_status not in {PageTemplateVersion.ModerationStatus.DRAFT, PageTemplateVersion.ModerationStatus.REJECTED}:
        version = get_or_create_editable_version(template)
    asset_ids = set(TemplateAsset.objects.filter(owner=template.owner).values_list("id", flat=True))
    normalized_schema, external_links = normalize_schema(
        cleaned_data["schema_json"],
        asset_ids=asset_ids,
        site_base_url=get_site_configuration().public_base_url,
    )
    html, payload = render_preview_fragment(template=template, version=version, schema_override=normalized_schema)
    version.schema_json = normalized_schema
    version.compiled_snapshot = {"page": payload["page"], "blocks": payload["blocks"]}
    version.rendered_preview_cache = html
    version.changelog = cleaned_data.get("changelog") or ""
    version.contains_external_links = bool(external_links)
    version.external_links_snapshot = external_links
    version.moderation_status = PageTemplateVersion.ModerationStatus.DRAFT
    version.submitted_at = None
    version.approved_at = None
    version.approved_by = None
    version.moderation_comment = ""
    version.save()
    template.current_version = version
    _sync_template_status(template)
    template.save()
    UserTemplateOwnership.objects.filter(user=template.owner, template=template).update(active_version=version)
    return version


@transaction.atomic
def submit_template_version(*, template: PageTemplate, version: PageTemplateVersion) -> PageTemplateVersion:
    if version.template_id != template.id:
        raise TemplateWorkflowError("Версия не принадлежит шаблону.")
    version.moderation_status = PageTemplateVersion.ModerationStatus.PENDING
    version.submitted_at = timezone.now()
    version.moderation_comment = ""
    version.save(update_fields=["moderation_status", "submitted_at", "moderation_comment", "updated_at"])
    template.current_version = version
    template.status = PageTemplate.Status.PENDING_MODERATION
    template.save(update_fields=["current_version", "status", "updated_at"])
    return version


@transaction.atomic
def approve_template_version(*, version: PageTemplateVersion, moderator, comment: str = "") -> PageTemplateVersion:
    template = version.template
    version.moderation_status = PageTemplateVersion.ModerationStatus.APPROVED
    version.approved_at = timezone.now()
    version.approved_by = moderator
    version.moderation_comment = comment or ""
    version.save(update_fields=["moderation_status", "approved_at", "approved_by", "moderation_comment", "updated_at"])
    template.approved_version = version
    template.current_version = version
    template.status = PageTemplate.Status.APPROVED
    template.save(update_fields=["approved_version", "current_version", "status", "updated_at"])
    UserTemplateOwnership.objects.filter(template=template).update(active_version=version)
    return version


@transaction.atomic
def reject_template_version(*, version: PageTemplateVersion, comment: str = "") -> PageTemplateVersion:
    template = version.template
    version.moderation_status = PageTemplateVersion.ModerationStatus.REJECTED
    version.moderation_comment = comment or ""
    version.save(update_fields=["moderation_status", "moderation_comment", "updated_at"])
    template.current_version = version
    _sync_template_status(template)
    template.save(update_fields=["current_version", "status", "updated_at"])
    return version


@transaction.atomic
def clone_page_template(*, template: PageTemplate, owner) -> PageTemplate:
    source_version = template.current_version or template.approved_version
    schema = source_version.schema_json if source_version else build_default_schema(template.title)
    cloned = PageTemplate.objects.create(
        owner=owner,
        title=f"{template.title} (copy)",
        slug=_generate_unique_slug(base_title=f"{template.title} copy"),
        description=template.description,
        page_type=template.page_type,
        visibility=PageTemplate.Visibility.PRIVATE,
        access_mode=PageTemplate.AccessMode.PERSONAL,
        is_free=True,
        price=Decimal("0.00"),
        currency=template.currency,
    )
    html, payload = render_preview_fragment(template=cloned, schema_override=schema)
    version = PageTemplateVersion.objects.create(
        template=cloned,
        version_number=1,
        schema_json=schema,
        compiled_snapshot={"page": payload["page"], "blocks": payload["blocks"]},
        rendered_preview_cache=html,
        moderation_status=PageTemplateVersion.ModerationStatus.DRAFT,
        contains_external_links=bool(payload["external_links"]),
        external_links_snapshot=payload["external_links"],
        changelog=f"Cloned from template #{template.id}",
    )
    cloned.current_version = version
    cloned.status = PageTemplate.Status.DRAFT
    cloned.save(update_fields=["current_version", "status", "updated_at"])
    UserTemplateOwnership.objects.create(user=owner, template=cloned, ownership_type=UserTemplateOwnership.OwnershipType.OWNER, active_version=version)
    return cloned


@transaction.atomic
def rollback_template_to_version(*, template: PageTemplate, version: PageTemplateVersion) -> PageTemplate:
    if version.template_id != template.id:
        raise TemplateWorkflowError("Выбранная версия не принадлежит шаблону.")
    if version.moderation_status != PageTemplateVersion.ModerationStatus.APPROVED:
        raise TemplateWorkflowError("Откат возможен только на approved-версию.")
    template.approved_version = version
    template.current_version = version
    template.status = PageTemplate.Status.APPROVED
    template.save(update_fields=["approved_version", "current_version", "status", "updated_at"])
    UserTemplateOwnership.objects.filter(template=template).update(active_version=version)
    return template
