from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0013_userprofile_customization_cache"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="verification_level",
            field=models.CharField(
                choices=[("none", "—"), ("verified", "Verified"), ("farmer", "Farmer"), ("legend", "Legend CaseUP")],
                db_index=True,
                default="none",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="VerificationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_level", models.CharField(choices=[("verified", "Verified"), ("farmer", "Farmer")], max_length=16)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("denied", "Denied")], db_index=True, default="pending", max_length=16)),
                ("user_message", models.TextField(blank=True, default="")),
                ("admin_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verification_requests", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_verification_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Verification request",
                "verbose_name_plural": "Verification requests",
            },
        ),
        migrations.AddIndex(
            model_name="verificationrequest",
            index=models.Index(fields=["user", "status"], name="prf_verreq_user_status_idx"),
        ),
        migrations.AddIndex(
            model_name="verificationrequest",
            index=models.Index(fields=["status", "created_at"], name="prf_verreq_status_idx"),
        ),
    ]
