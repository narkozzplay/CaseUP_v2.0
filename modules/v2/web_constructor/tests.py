from __future__ import annotations

import json
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from modules.v2.profiles.models import UserRole
from modules.v2.system.models import SiteConfiguration

from .forms import PageTemplateEditorForm
from .models import EditablePage, PageTemplate, PageTemplateVersion, SandboxSession, TemplateAsset, TemplateVariable, UserTemplateOwnership
from .services import (
    approve_template_version,
    clone_page_template,
    create_page_template,
    get_or_create_editable_version,
    normalize_schema,
    render_preview_fragment,
    rollback_template_to_version,
    save_template_draft,
    submit_template_version,
    sync_editable_pages,
    sync_template_variables,
)

User = get_user_model()


GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


class WebConstructorTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="builder", password="StrongPass123")
        self.admin = User.objects.create_user(username="moderator", password="StrongPass123")
        self.admin.profile_v2.role = UserRole.ADMIN
        self.admin.profile_v2.save(update_fields=["role"])

    def _login(self, user):
        self.client.force_login(user)

    def _create_template(self):
        return create_page_template(
            owner=self.user,
            cleaned_data={
                "title": "Builder Page",
                "slug": "builder-page",
                "description": "Test page",
                "page_type": PageTemplate.PageType.LANDING,
                "visibility": PageTemplate.Visibility.PRIVATE,
                "access_mode": PageTemplate.AccessMode.PERSONAL,
                "is_free": True,
                "price": Decimal("0.00"),
                "currency": "RUB",
            },
        )

    def test_create_template_creates_owner_binding_and_v1(self):
        template = self._create_template()
        self.assertEqual(template.current_version.version_number, 1)
        self.assertEqual(template.current_version.moderation_status, PageTemplateVersion.ModerationStatus.DRAFT)
        self.assertTrue(UserTemplateOwnership.objects.filter(user=self.user, template=template, ownership_type="owner").exists())

    def test_save_draft_preserves_approved_version_until_reapproval(self):
        template = self._create_template()
        submit_template_version(template=template, version=template.current_version)
        approve_template_version(version=template.current_version, moderator=self.admin)
        approved_version_id = template.approved_version_id

        editable = get_or_create_editable_version(template)
        self.assertNotEqual(editable.id, approved_version_id)

        updated = save_template_draft(
            template=template,
            version=editable,
            cleaned_data={
                "title": template.title,
                "slug": template.slug,
                "description": template.description,
                "page_type": template.page_type,
                "visibility": template.visibility,
                "access_mode": template.access_mode,
                "is_free": True,
                "price": Decimal("0.00"),
                "currency": template.currency,
                "schema_json": {
                    "page": {"style": {"theme": "dark", "accent": "blue", "width": "standard", "spacing": "normal", "font_scale": "md", "surface": "plain", "radius": "md", "background_asset_id": None}},
                    "blocks": [
                        {
                            "id": "blk_1",
                            "type": "hero",
                            "visible": True,
                            "data": {"eyebrow": "Live", "title": "New hero", "text": "Changed", "primary_label": "Go", "primary_url": "/", "secondary_label": "Docs", "secondary_url": "/docs/", "image_asset_id": None, "image_url": ""},
                            "style": {"variant": "accent", "align": "left", "padding": "lg"},
                        }
                    ],
                },
                "changelog": "Updated hero",
            },
        )
        template.refresh_from_db()
        self.assertEqual(template.approved_version_id, approved_version_id)
        self.assertEqual(updated.moderation_status, PageTemplateVersion.ModerationStatus.DRAFT)
        self.assertEqual(template.status, PageTemplate.Status.APPROVED)

    def test_clone_template_creates_private_draft_copy(self):
        template = self._create_template()
        cloned = clone_page_template(template=template, owner=self.user)
        self.assertNotEqual(cloned.id, template.id)
        self.assertEqual(cloned.visibility, PageTemplate.Visibility.PRIVATE)
        self.assertEqual(cloned.current_version.version_number, 1)
        self.assertEqual(cloned.current_version.moderation_status, PageTemplateVersion.ModerationStatus.DRAFT)

    def test_rollback_switches_live_version(self):
        template = self._create_template()
        submit_template_version(template=template, version=template.current_version)
        approve_template_version(version=template.current_version, moderator=self.admin)
        approved_v1 = template.approved_version
        editable = get_or_create_editable_version(template)
        editable.schema_json["blocks"][0]["data"]["title"] = "Version 2"
        editable.save(update_fields=["schema_json", "updated_at"])
        submit_template_version(template=template, version=editable)
        approve_template_version(version=editable, moderator=self.admin)
        template.refresh_from_db()
        self.assertEqual(template.approved_version_id, editable.id)
        rollback_template_to_version(template=template, version=approved_v1)
        template.refresh_from_db()
        self.assertEqual(template.approved_version_id, approved_v1.id)

    def test_normalize_schema_collects_external_links(self):
        normalized, links = normalize_schema(
            {
                "page": {"style": {"theme": "light", "accent": "blue", "width": "standard", "spacing": "normal", "font_scale": "md", "surface": "plain", "radius": "md"}},
                "blocks": [
                    {
                        "id": "blk_1",
                        "type": "hero",
                        "visible": True,
                        "data": {
                            "eyebrow": "x",
                            "title": "x",
                            "text": "x",
                            "primary_label": "Open",
                            "primary_url": "https://example.com/outside",
                            "secondary_label": "Docs",
                            "secondary_url": "/docs/",
                            "image_asset_id": None,
                            "image_url": "https://cdn.example.com/img.png",
                        },
                        "style": {"variant": "accent", "align": "left", "padding": "lg"},
                    }
                ],
            },
            asset_ids=set(),
            site_base_url="https://caseup.test",
        )
        self.assertEqual(normalized["blocks"][0]["data"]["primary_url"], "https://example.com/outside")
        self.assertIn("https://example.com/outside", links)
        self.assertIn("https://cdn.example.com/img.png", links)

    def test_preview_fragment_endpoint_requires_owner_and_renders_safe_html(self):
        template = self._create_template()
        self._login(self.user)
        response = self.client.post(
            reverse("web_constructor:preview_fragment", kwargs={"template_uuid": template.template_uuid}),
            {"schema_json": json.dumps(template.current_version.schema_json)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Builder Page")

    def test_public_render_uses_only_approved_version_for_foreign_user(self):
        template = self._create_template()
        submit_template_version(template=template, version=template.current_version)
        approve_template_version(version=template.current_version, moderator=self.admin)
        editable = get_or_create_editable_version(template)
        editable.schema_json["blocks"][0]["data"]["title"] = "Leaked draft"
        editable.save(update_fields=["schema_json", "updated_at"])
        template.visibility = PageTemplate.Visibility.PUBLIC
        template.save(update_fields=["visibility", "updated_at"])

        stranger = User.objects.create_user(username="stranger", password="StrongPass123")
        self.client.force_login(stranger)
        response = self.client.get(reverse("web_constructor:render", kwargs={"template_uuid": template.template_uuid}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Leaked draft")
        self.assertContains(response, "Builder Page")

    def test_asset_upload_page_creates_asset(self):
        template = self._create_template()
        self._login(self.user)
        upload = SimpleUploadedFile("tiny.gif", GIF_BYTES, content_type="image/gif")
        response = self.client.post(
            reverse("web_constructor:upload_asset", kwargs={"template_uuid": template.template_uuid}),
            {"title": "Tiny", "alt_text": "tiny", "asset_type": "image", "file": upload},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(TemplateAsset.objects.filter(owner=self.user, template=template, title="Tiny").exists())

    @override_settings(WEB_CONSTRUCTOR_MAX_IMAGE_UPLOAD_BYTES=16)
    def test_asset_upload_rejects_oversized_file(self):
        template = self._create_template()
        self._login(self.user)
        upload = SimpleUploadedFile("tiny.gif", GIF_BYTES, content_type="image/gif")
        response = self.client.post(
            reverse("web_constructor:upload_asset", kwargs={"template_uuid": template.template_uuid}),
            {"title": "Too large", "alt_text": "large", "asset_type": "image", "file": upload},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TemplateAsset.objects.filter(owner=self.user, template=template, title="Too large").exists())
        self.assertContains(response, "Не удалось загрузить asset")

    def test_asset_upload_rejects_malformed_image(self):
        template = self._create_template()
        self._login(self.user)
        upload = SimpleUploadedFile("broken.png", b"not-an-image", content_type="image/png")
        response = self.client.post(
            reverse("web_constructor:upload_asset", kwargs={"template_uuid": template.template_uuid}),
            {"title": "Broken", "alt_text": "broken", "asset_type": "image", "file": upload},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TemplateAsset.objects.filter(owner=self.user, template=template, title="Broken").exists())
        self.assertContains(response, "Не удалось загрузить asset")

    def test_editor_form_rejects_malformed_cover_image(self):
        template = self._create_template()
        bad_cover = SimpleUploadedFile("broken.png", b"not-an-image", content_type="image/png")
        form = PageTemplateEditorForm(
            data={
                "title": template.title,
                "slug": template.slug,
                "description": template.description,
                "page_type": template.page_type,
                "visibility": template.visibility,
                "access_mode": template.access_mode,
                "is_free": "on",
                "price": "0.00",
                "currency": template.currency,
                "schema_json": json.dumps(template.current_version.schema_json),
                "changelog": "",
            },
            files={"cover_image": bad_cover},
            instance=template,
            initial_schema=template.current_version.schema_json,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("cover_image", form.errors)


class WebConstructorBuilderFoundationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="architect", password="StrongPass123")

    def test_sync_editable_pages_registers_real_project_routes(self):
        pages = sync_editable_pages()
        routes = {page.route_path for page in pages}
        self.assertIn("/", routes)
        self.assertNotIn("/constructor/", routes)
        self.assertIn("/dashboard-preview/", routes)
        self.assertTrue(EditablePage.objects.filter(route_path="/profile/").exists())

    def test_builder_variables_endpoint_supports_search(self):
        self.client.force_login(self.user)
        sync_template_variables()
        response = self.client.get(reverse("web_constructor:builder_variables"), {"q": "balance"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["total"], 1)
        self.assertTrue(any(item["key"] == "user.balance" for item in payload["items"]))

    def test_builder_save_draft_creates_isolated_sandbox_session(self):
        self.client.force_login(self.user)
        sync_editable_pages()
        page = EditablePage.objects.get(route_path="/dashboard-preview/")
        response = self.client.post(
            reverse("web_constructor:builder_save_draft", kwargs={"page_id": page.id}),
            data=json.dumps({"selected_area_key": "hero", "areas": {"hero": {"content": "Draft hero"}}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        session = SandboxSession.objects.get(owner=self.user)
        self.assertEqual(session.editable_page_id, page.id)
        self.assertEqual(session.draft_payload["pages"][page.route_key]["areas"]["hero"]["content"], "Draft hero")

    @override_settings(SITE_BASE_URL="http://caseup.localhost:8000", CASEUP_LAB_BASE_URL="http://lib.caseup.localhost:8000", ALLOWED_HOSTS=["testserver", "caseup.localhost", "lib.caseup.localhost"])
    def test_builder_editor_page_renders_foundation_shell(self):
        # public_base_url is seeded from SITE_BASE_URL of the env the test DB was built with.
        SiteConfiguration.objects.update(public_base_url="")
        self.client.force_login(self.user)
        sync_editable_pages()
        sync_template_variables()
        page = EditablePage.objects.get(route_path="/")
        response = self.client.get(
            reverse("web_constructor:builder_editor", kwargs={"page_id": page.id}),
            HTTP_HOST="lib.caseup.localhost:8000",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CaseUP Lab")
        self.assertContains(response, "Project pages")
        self.assertContains(response, "Variables")
        self.assertContains(response, 'data-preview-url="/"')
        self.assertContains(response, 'data-preview-open-url="http://caseup.localhost:8000/"')
        self.assertContains(response, 'href="http://caseup.localhost:8000/dashboard-preview/"')
        self.assertNotContains(response, "data-dashboard-shell")

    @override_settings(SITE_BASE_URL="http://caseup.localhost:8000", CASEUP_LAB_BASE_URL="http://lib.caseup.localhost:8000", ALLOWED_HOSTS=["testserver", "caseup.localhost", "lib.caseup.localhost"])
    def test_builder_pages_redirect_to_lab_host(self):
        self.client.force_login(self.user)
        sync_editable_pages()
        page = EditablePage.objects.get(route_path="/")

        response = self.client.get(
            reverse("web_constructor:builder_editor", kwargs={"page_id": page.id}),
            HTTP_HOST="caseup.localhost:8000",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"http://lib.caseup.localhost:8000{reverse('web_constructor:builder_editor', kwargs={'page_id': page.id})}")
