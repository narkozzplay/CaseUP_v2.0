from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("steam_trading", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="steampurchase",
            name="unlock_notification_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="steampurchase",
            name="unlock_notified_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="steampurchase",
            name="unlocked_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="steampurchase",
            index=models.Index(fields=["unlocked_at", "unlock_notified_at"], name="steam_tradi_unlocke_1e912d_idx"),
        ),
    ]
