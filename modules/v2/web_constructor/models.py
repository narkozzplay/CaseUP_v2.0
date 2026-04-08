from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from modules.v2.system.models import SiteConfiguration

User = settings.AUTH_USER_MODEL


class PageTemplate(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        UNLISTED = "unlisted", "Unlisted"
        PUBLIC = "public", "Public"

    class AccessMode(models.TextChoices):
        PERSONAL = "personal", "Personal"
        SHARED = "shared", "Shared"
        MARKETPLACE = "marketplace", "Marketplace"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_MODERATION = "pending_moderation", "Pending moderation"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    class PageType(models.TextChoices):
        LANDING = "landing", "Landing"
        HOMEPAGE = "homepage", "Homepage"
        DASHBOARD_PREVIEW = "dashboard_preview", "Dashboard preview"
        PROFILE_PUBLIC = "profile_public", "Profile-like public page"
        GENERIC = "generic", "Generic"

    class PricingMode(models.TextChoices):
        FREE = "free", "Free"
        FIXED = "fixed", "Fixed"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="page_templates")
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    template_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    description = models.TextField(blank=True, default="")
    page_type = models.CharField(max_length=32, choices=PageType.choices, default=PageType.LANDING, db_index=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE, db_index=True)
    access_mode = models.CharField(max_length=16, choices=AccessMode.choices, default=AccessMode.PERSONAL, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    current_version = models.ForeignKey(
        "PageTemplateVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_templates",
    )
    approved_version = models.ForeignKey(
        "PageTemplateVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_for_templates",
    )
    cover_image = models.ImageField(upload_to="web_constructor/covers/", null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=8, choices=SiteConfiguration.CURRENCY_CHOICES, default=SiteConfiguration.CURRENCY_RUB)
    pricing_mode = models.CharField(max_length=16, choices=PricingMode.choices, default=PricingMode.FREE, db_index=True)
    is_free = models.BooleanField(default=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=["owner", "status", "updated_at"]),
            models.Index(fields=["visibility", "status", "updated_at"]),
        ]

    def clean(self):
        self.title = (self.title or "").strip()
        self.description = (self.description or "").strip()
        self.slug = slugify(self.slug or self.title or str(self.template_uuid)[:8])[:160]
        self.currency = (self.currency or SiteConfiguration.CURRENCY_RUB).strip().upper()
        if self.currency not in dict(SiteConfiguration.CURRENCY_CHOICES):
            raise ValidationError({"currency": "Недопустимая валюта шаблона."})
        if self.is_free:
            self.price = Decimal("0.00")
            self.pricing_mode = self.PricingMode.FREE
        else:
            if self.price is None or self.price <= 0:
                raise ValidationError({"price": "Для платного шаблона цена должна быть больше нуля."})
            self.pricing_mode = self.PricingMode.FIXED
        if self.current_version and self.current_version.template_id != self.id:
            raise ValidationError({"current_version": "Current version не принадлежит шаблону."})
        if self.approved_version and self.approved_version.template_id != self.id:
            raise ValidationError({"approved_version": "Approved version не принадлежит шаблону."})
        if self.approved_version and self.approved_version.moderation_status != PageTemplateVersion.ModerationStatus.APPROVED:
            raise ValidationError({"approved_version": "Approved version должна иметь статус approved."})

    def save(self, *args, **kwargs):
        self.slug = slugify(self.slug or self.title or str(self.template_uuid)[:8])[:160]
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class PageTemplateVersion(models.Model):
    class ModerationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending moderation"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    template = models.ForeignKey(PageTemplate, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    schema_json = models.JSONField(default=dict, blank=True)
    compiled_snapshot = models.JSONField(default=dict, blank=True)
    rendered_preview_cache = models.TextField(blank=True, default="")
    changelog = models.TextField(blank=True, default="")
    moderation_status = models.CharField(max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.DRAFT, db_index=True)
    moderation_comment = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_page_template_versions")
    contains_external_links = models.BooleanField(default=False, db_index=True)
    external_links_snapshot = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-version_number", "-id")
        constraints = [
            models.UniqueConstraint(fields=["template", "version_number"], name="web_constructor_template_version_number_uniq"),
        ]
        indexes = [
            models.Index(fields=["template", "moderation_status", "version_number"]),
        ]

    def clean(self):
        if self.version_number <= 0:
            raise ValidationError({"version_number": "Version number должен быть больше нуля."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.template.title} v{self.version_number}"


class UserTemplateOwnership(models.Model):
    class OwnershipType(models.TextChoices):
        OWNER = "owner", "Owner"
        PURCHASED = "purchased", "Purchased"
        GRANTED = "granted", "Granted"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="template_ownerships")
    template = models.ForeignKey(PageTemplate, on_delete=models.CASCADE, related_name="ownerships")
    ownership_type = models.CharField(max_length=16, choices=OwnershipType.choices, default=OwnershipType.OWNER, db_index=True)
    active_version = models.ForeignKey(PageTemplateVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name="ownership_bindings")
    installed_at = models.DateTimeField(auto_now_add=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-installed_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["user", "template"], name="web_constructor_ownership_user_template_uniq"),
        ]

    def clean(self):
        if self.active_version and self.active_version.template_id != self.template_id:
            raise ValidationError({"active_version": "Версия не принадлежит указанному шаблону."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user_id}:{self.template_id}:{self.ownership_type}"


class TemplateAsset(models.Model):
    class AssetType(models.TextChoices):
        IMAGE = "image", "Image"
        BANNER = "banner", "Banner"
        BACKGROUND = "background", "Background"
        ICON = "icon", "Icon"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="template_assets")
    template = models.ForeignKey(PageTemplate, null=True, blank=True, on_delete=models.CASCADE, related_name="assets")
    title = models.CharField(max_length=160, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    file = models.ImageField(upload_to="web_constructor/assets/")
    asset_type = models.CharField(max_length=16, choices=AssetType.choices, default=AssetType.IMAGE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["owner", "asset_type", "created_at"]),
        ]

    def clean(self):
        if self.template_id and self.template.owner_id != self.owner_id:
            raise ValidationError({"template": "Asset можно привязать только к своему шаблону."})

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        self.alt_text = (self.alt_text or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or self.file.name


class EditablePage(models.Model):
    class SourceType(models.TextChoices):
        URLCONF = "urlconf", "URLConf"
        MANUAL = "manual", "Manual"

    route_key = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=160)
    url_name = models.CharField(max_length=255, blank=True, default="")
    route_path = models.CharField(max_length=255)
    template_name = models.CharField(max_length=255, blank=True, default="")
    layout_template = models.CharField(max_length=255, blank=True, default="")
    view_path = models.CharField(max_length=255, blank=True, default="")
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.URLCONF, db_index=True)
    requires_auth = models.BooleanField(default=True, db_index=True)
    is_dynamic = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    meta_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "route_path", "id")
        indexes = [
            models.Index(fields=["is_active", "sort_order"]),
            models.Index(fields=["url_name"]),
        ]

    def clean(self):
        self.route_key = (self.route_key or "").strip()
        self.title = (self.title or self.route_key or self.route_path).strip()
        self.route_path = (self.route_path or "").strip()
        self.url_name = (self.url_name or "").strip()
        self.template_name = (self.template_name or "").strip()
        self.layout_template = (self.layout_template or "").strip()
        self.view_path = (self.view_path or "").strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class EditableArea(models.Model):
    class AreaType(models.TextChoices):
        SECTION = "section", "Section"
        BLOCK = "block", "Block"
        HEADER = "header", "Header"
        SIDEBAR = "sidebar", "Sidebar"
        FOOTER = "footer", "Footer"
        HERO = "hero", "Hero"
        CONTENT = "content", "Content"

    page = models.ForeignKey(EditablePage, on_delete=models.CASCADE, related_name="areas")
    area_key = models.CharField(max_length=120)
    label = models.CharField(max_length=160)
    area_type = models.CharField(max_length=24, choices=AreaType.choices, default=AreaType.SECTION, db_index=True)
    template_path = models.CharField(max_length=255, blank=True, default="")
    default_content = models.JSONField(default=dict, blank=True)
    default_styles = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    is_visible_by_default = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    meta_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("page", "sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["page", "area_key"], name="wc_editable_area_page_key_uniq"),
        ]

    def clean(self):
        self.area_key = (self.area_key or "").strip()
        self.label = (self.label or self.area_key).strip()
        self.template_path = (self.template_path or "").strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.page.route_key}:{self.area_key}"


class TemplateVariable(models.Model):
    class ValueType(models.TextChoices):
        STRING = "string", "String"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Boolean"
        IMAGE = "image", "Image"
        URL = "url", "URL"
        JSON = "json", "JSON"

    key = models.CharField(max_length=160, unique=True)
    label = models.CharField(max_length=160)
    group = models.CharField(max_length=80, db_index=True)
    source = models.CharField(max_length=120)
    value_type = models.CharField(max_length=16, choices=ValueType.choices, default=ValueType.STRING, db_index=True)
    example_value = models.CharField(max_length=255, blank=True, default="")
    resolver_hint = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    allowed_pages = models.ManyToManyField(EditablePage, blank=True, related_name="allowed_variables")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("group", "label", "key")
        indexes = [
            models.Index(fields=["group", "is_active"]),
        ]

    def clean(self):
        self.key = (self.key or "").strip()
        self.label = (self.label or self.key).strip()
        self.group = (self.group or "general").strip()
        self.source = (self.source or "runtime").strip()
        self.example_value = (self.example_value or "").strip()
        self.resolver_hint = (self.resolver_hint or "").strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.key


class StylePreset(models.Model):
    class Scope(models.TextChoices):
        PAGE = "page", "Page"
        AREA = "area", "Area"
        THEME = "theme", "Theme"

    key = models.CharField(max_length=120, unique=True)
    label = models.CharField(max_length=160)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.PAGE, db_index=True)
    description = models.TextField(blank=True, default="")
    config_json = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("scope", "label", "key")

    def clean(self):
        self.key = (self.key or "").strip()
        self.label = (self.label or self.key).strip()
        duplicates = StylePreset.objects.exclude(pk=self.pk)
        if self.is_default and duplicates.filter(scope=self.scope, is_default=True).exists():
            raise ValidationError({"is_default": "Default preset already exists for this scope."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.label


class SandboxSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="web_constructor_sandbox_sessions")
    title = models.CharField(max_length=160, default="Sandbox draft")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    editable_page = models.ForeignKey(EditablePage, null=True, blank=True, on_delete=models.SET_NULL, related_name="sandbox_sessions")
    legacy_template = models.ForeignKey(PageTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="sandbox_sessions")
    draft_payload = models.JSONField(default=dict, blank=True)
    published_payload = models.JSONField(default=dict, blank=True)
    preview_url = models.CharField(max_length=255, blank=True, default="")
    last_saved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=["owner", "status", "updated_at"]),
        ]

    def clean(self):
        self.title = (self.title or "Sandbox draft").strip()
        self.preview_url = (self.preview_url or "").strip()
        if self.legacy_template_id and self.legacy_template.owner_id != self.owner_id:
            raise ValidationError({"legacy_template": "Legacy template must belong to sandbox owner."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.owner_id}:{self.title}"


class SandboxAssetBinding(models.Model):
    session = models.ForeignKey(SandboxSession, on_delete=models.CASCADE, related_name="asset_bindings")
    asset = models.ForeignKey(TemplateAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="sandbox_bindings")
    page = models.ForeignKey(EditablePage, null=True, blank=True, on_delete=models.CASCADE, related_name="asset_bindings")
    area = models.ForeignKey(EditableArea, null=True, blank=True, on_delete=models.CASCADE, related_name="asset_bindings")
    binding_key = models.CharField(max_length=120)
    role = models.CharField(max_length=80, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    meta_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("binding_key", "id")
        constraints = [
            models.UniqueConstraint(fields=["session", "binding_key"], name="wc_sandbox_asset_binding_session_key_uniq"),
        ]

    def clean(self):
        self.binding_key = (self.binding_key or "").strip()
        self.role = (self.role or "").strip()
        self.alt_text = (self.alt_text or "").strip()
        if self.asset_id and self.asset.owner_id != self.session.owner_id:
            raise ValidationError({"asset": "Asset must belong to sandbox owner."})
        if self.area_id and self.page_id and self.area.page_id != self.page_id:
            raise ValidationError({"area": "Editable area does not belong to page."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.session_id}:{self.binding_key}"
