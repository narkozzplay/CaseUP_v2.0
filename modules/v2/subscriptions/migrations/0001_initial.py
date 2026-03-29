# Generated manually for CaseUP subscriptions foundation.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionFeature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("module_key", models.CharField(blank=True, default="", max_length=64)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Subscription feature",
                "verbose_name_plural": "Subscription features",
                "ordering": ("sort_order", "name", "id"),
            },
        ),
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("level", models.PositiveSmallIntegerField(unique=True)),
                ("description", models.TextField(blank=True, default="")),
                ("ui_badge", models.CharField(blank=True, default="", max_length=64)),
                ("ui_features", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("features", models.ManyToManyField(blank=True, related_name="plans", to="subscriptions.subscriptionfeature")),
            ],
            options={
                "verbose_name": "Subscription plan",
                "verbose_name_plural": "Subscription plans",
                "ordering": ("sort_order", "level", "id"),
            },
        ),
        migrations.CreateModel(
            name="UserSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("active", "Active"), ("canceled", "Canceled"), ("expired", "Expired")], db_index=True, default="active", max_length=16)),
                ("starts_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="user_subscriptions", to="subscriptions.subscriptionplan")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscriptions_v2", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "User subscription",
                "verbose_name_plural": "User subscriptions",
                "ordering": ("-starts_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["user", "status", "starts_at"], name="sub_usr_stat_start_idx"),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["status", "expires_at"], name="sub_stat_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="usersubscription",
            index=models.Index(fields=["plan", "status"], name="sub_plan_stat_idx"),
        ),
    ]
