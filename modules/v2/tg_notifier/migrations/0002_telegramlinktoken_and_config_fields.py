import modules.v2.tg_notifier.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tg_notifier", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="bot_username",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="webhook_secret",
            field=models.CharField(blank=True, default=modules.v2.tg_notifier.models.generate_webhook_secret, max_length=64),
        ),
        migrations.CreateModel(
            name="TelegramLinkToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(db_index=True, default=modules.v2.tg_notifier.models.generate_link_token, max_length=32, unique=True)),
                ("expires_at", models.DateTimeField(default=modules.v2.tg_notifier.models.default_link_expiry)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_link_tokens", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Telegram link token",
                "verbose_name_plural": "Telegram link tokens",
                "indexes": [
                    models.Index(fields=["user", "created_at"], name="tg_notifier_user_id_42b40c_idx"),
                    models.Index(fields=["expires_at"], name="tg_notifier_expires_b0a9f6_idx"),
                ],
            },
        ),
    ]