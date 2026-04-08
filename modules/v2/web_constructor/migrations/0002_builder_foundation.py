from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("web_constructor", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EditablePage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("route_key", models.CharField(max_length=255, unique=True)),
                ("title", models.CharField(max_length=160)),
                ("url_name", models.CharField(blank=True, default="", max_length=255)),
                ("route_path", models.CharField(max_length=255)),
                ("template_name", models.CharField(blank=True, default="", max_length=255)),
                ("layout_template", models.CharField(blank=True, default="", max_length=255)),
                ("view_path", models.CharField(blank=True, default="", max_length=255)),
                ("source_type", models.CharField(choices=[("urlconf", "URLConf"), ("manual", "Manual")], db_index=True, default="urlconf", max_length=16)),
                ("requires_auth", models.BooleanField(db_index=True, default=True)),
                ("is_dynamic", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("meta_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ("sort_order", "route_path", "id"),
                "indexes": [models.Index(fields=["is_active", "sort_order"], name="wc_editable_is_acti_483d5d_idx"), models.Index(fields=["url_name"], name="wc_editable_url_nam_0d9d62_idx")],
            },
        ),
        migrations.CreateModel(
            name="StylePreset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=120, unique=True)),
                ("label", models.CharField(max_length=160)),
                ("scope", models.CharField(choices=[("page", "Page"), ("area", "Area"), ("theme", "Theme")], db_index=True, default="page", max_length=16)),
                ("description", models.TextField(blank=True, default="")),
                ("config_json", models.JSONField(blank=True, default=dict)),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("scope", "label", "key")},
        ),
        migrations.CreateModel(
            name="EditableArea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("area_key", models.CharField(max_length=120)),
                ("label", models.CharField(max_length=160)),
                ("area_type", models.CharField(choices=[("section", "Section"), ("block", "Block"), ("header", "Header"), ("sidebar", "Sidebar"), ("footer", "Footer"), ("hero", "Hero"), ("content", "Content")], db_index=True, default="section", max_length=24)),
                ("template_path", models.CharField(blank=True, default="", max_length=255)),
                ("default_content", models.JSONField(blank=True, default=dict)),
                ("default_styles", models.JSONField(blank=True, default=dict)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("is_visible_by_default", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("meta_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="areas", to="web_constructor.editablepage")),
            ],
            options={
                "ordering": ("page", "sort_order", "id"),
                "constraints": [models.UniqueConstraint(fields=("page", "area_key"), name="wc_editable_area_page_key_uniq")],
            },
        ),
        migrations.CreateModel(
            name="SandboxSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(default="Sandbox draft", max_length=160)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")], db_index=True, default="draft", max_length=16)),
                ("draft_payload", models.JSONField(blank=True, default=dict)),
                ("published_payload", models.JSONField(blank=True, default=dict)),
                ("preview_url", models.CharField(blank=True, default="", max_length=255)),
                ("last_saved_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("editable_page", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sandbox_sessions", to="web_constructor.editablepage")),
                ("legacy_template", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sandbox_sessions", to="web_constructor.pagetemplate")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="web_constructor_sandbox_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-updated_at", "-id"),
                "indexes": [models.Index(fields=["owner", "status", "updated_at"], name="wc_sandbox_owner_i_0bb511_idx")],
            },
        ),
        migrations.CreateModel(
            name="TemplateVariable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=160, unique=True)),
                ("label", models.CharField(max_length=160)),
                ("group", models.CharField(db_index=True, max_length=80)),
                ("source", models.CharField(max_length=120)),
                ("value_type", models.CharField(choices=[("string", "String"), ("number", "Number"), ("boolean", "Boolean"), ("image", "Image"), ("url", "URL"), ("json", "JSON")], db_index=True, default="string", max_length=16)),
                ("example_value", models.CharField(blank=True, default="", max_length=255)),
                ("resolver_hint", models.CharField(blank=True, default="", max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("allowed_pages", models.ManyToManyField(blank=True, related_name="allowed_variables", to="web_constructor.editablepage")),
            ],
            options={
                "ordering": ("group", "label", "key"),
                "indexes": [models.Index(fields=["group", "is_active"], name="wc_template_group_faef83_idx")],
            },
        ),
        migrations.CreateModel(
            name="SandboxAssetBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("binding_key", models.CharField(max_length=120)),
                ("role", models.CharField(blank=True, default="", max_length=80)),
                ("alt_text", models.CharField(blank=True, default="", max_length=255)),
                ("meta_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("area", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="asset_bindings", to="web_constructor.editablearea")),
                ("asset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sandbox_bindings", to="web_constructor.templateasset")),
                ("page", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="asset_bindings", to="web_constructor.editablepage")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="asset_bindings", to="web_constructor.sandboxsession")),
            ],
            options={
                "ordering": ("binding_key", "id"),
                "constraints": [models.UniqueConstraint(fields=("session", "binding_key"), name="wc_sandbox_asset_binding_session_key_uniq")],
            },
        ),
    ]
