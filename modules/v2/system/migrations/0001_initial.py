from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ModuleState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=64, unique=True)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Module state",
                "verbose_name_plural": "Module states",
                "ordering": ("key",),
            },
        ),
        migrations.CreateModel(
            name="SiteConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("site_name", models.CharField(default="CaseUP", max_length=120)),
                ("site_currency_code", models.CharField(default="RUB", max_length=16)),
                ("site_currency_symbol", models.CharField(default="₽", max_length=8)),
                ("support_email", models.EmailField(blank=True, default="", max_length=254)),
                ("public_base_url", models.URLField(blank=True, default="", max_length=255)),
                ("maintenance_mode_enabled", models.BooleanField(default=False)),
                ("maintenance_message", models.TextField(blank=True, default="")),
                ("registration_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Site configuration",
                "verbose_name_plural": "Site configuration",
            },
        ),
    ]
