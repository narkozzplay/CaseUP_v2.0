from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PageTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("slug", models.SlugField(max_length=160, unique=True)),
                ("template_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("description", models.TextField(blank=True, default="")),
                ("page_type", models.CharField(choices=[("landing", "Landing"), ("homepage", "Homepage"), ("dashboard_preview", "Dashboard preview"), ("profile_public", "Profile-like public page"), ("generic", "Generic")], db_index=True, default="landing", max_length=32)),
                ("visibility", models.CharField(choices=[("private", "Private"), ("unlisted", "Unlisted"), ("public", "Public")], db_index=True, default="private", max_length=16)),
                ("access_mode", models.CharField(choices=[("personal", "Personal"), ("shared", "Shared"), ("marketplace", "Marketplace")], db_index=True, default="personal", max_length=16)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("pending_moderation", "Pending moderation"), ("approved", "Approved"), ("rejected", "Rejected"), ("archived", "Archived")], db_index=True, default="draft", max_length=24)),
                ("cover_image", models.ImageField(blank=True, null=True, upload_to="web_constructor/covers/")),
                ("price", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("currency", models.CharField(choices=[("RUB", "Российский рубль (RUB)"), ("USD", "Доллар США (USD)"), ("EUR", "Евро (EUR)"), ("GBP", "Фунт стерлингов (GBP)"), ("KZT", "Казахстанский тенге (KZT)"), ("BYN", "Белорусский рубль (BYN)")], default="RUB", max_length=8)),
                ("pricing_mode", models.CharField(choices=[("free", "Free"), ("fixed", "Fixed")], db_index=True, default="free", max_length=16)),
                ("is_free", models.BooleanField(db_index=True, default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="page_templates", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-updated_at", "-id"),
                "indexes": [models.Index(fields=["owner", "status", "updated_at"], name="web_constru_owner_i_b4cf7e_idx"), models.Index(fields=["visibility", "status", "updated_at"], name="web_constru_visibil_aef962_idx")],
            },
        ),
        migrations.CreateModel(
            name="PageTemplateVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_number", models.PositiveIntegerField()),
                ("schema_json", models.JSONField(blank=True, default=dict)),
                ("compiled_snapshot", models.JSONField(blank=True, default=dict)),
                ("rendered_preview_cache", models.TextField(blank=True, default="")),
                ("changelog", models.TextField(blank=True, default="")),
                ("moderation_status", models.CharField(choices=[("draft", "Draft"), ("pending", "Pending moderation"), ("approved", "Approved"), ("rejected", "Rejected")], db_index=True, default="draft", max_length=16)),
                ("moderation_comment", models.TextField(blank=True, default="")),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("contains_external_links", models.BooleanField(db_index=True, default=False)),
                ("external_links_snapshot", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_page_template_versions", to=settings.AUTH_USER_MODEL)),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="web_constructor.pagetemplate")),
            ],
            options={
                "ordering": ("-version_number", "-id"),
                "indexes": [models.Index(fields=["template", "moderation_status", "version_number"], name="web_constru_templat_e1c181_idx")],
                "constraints": [models.UniqueConstraint(fields=("template", "version_number"), name="web_constructor_template_version_number_uniq")],
            },
        ),
        migrations.CreateModel(
            name="TemplateAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(blank=True, default="", max_length=160)),
                ("alt_text", models.CharField(blank=True, default="", max_length=255)),
                ("file", models.ImageField(upload_to="web_constructor/assets/")),
                ("asset_type", models.CharField(choices=[("image", "Image"), ("banner", "Banner"), ("background", "Background"), ("icon", "Icon")], db_index=True, default="image", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="template_assets", to=settings.AUTH_USER_MODEL)),
                ("template", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="assets", to="web_constructor.pagetemplate")),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "indexes": [models.Index(fields=["owner", "asset_type", "created_at"], name="web_constru_owner_i_8ec13c_idx")],
            },
        ),
        migrations.CreateModel(
            name="UserTemplateOwnership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ownership_type", models.CharField(choices=[("owner", "Owner"), ("purchased", "Purchased"), ("granted", "Granted")], db_index=True, default="owner", max_length=16)),
                ("installed_at", models.DateTimeField(auto_now_add=True)),
                ("purchased_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("active_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ownership_bindings", to="web_constructor.pagetemplateversion")),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ownerships", to="web_constructor.pagetemplate")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="template_ownerships", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-installed_at", "-id"),
                "constraints": [models.UniqueConstraint(fields=("user", "template"), name="web_constructor_ownership_user_template_uniq")],
            },
        ),
        migrations.AddField(
            model_name="pagetemplate",
            name="approved_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_for_templates", to="web_constructor.pagetemplateversion"),
        ),
        migrations.AddField(
            model_name="pagetemplate",
            name="current_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="current_for_templates", to="web_constructor.pagetemplateversion"),
        ),
    ]
