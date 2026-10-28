from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tg_notifier", "0011_admin_event_notices"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="utm_source",
            field=models.CharField(blank=True, default="telegram", max_length=64),
        ),
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="channel_utm_campaign",
            field=models.CharField(blank=True, default="price_digest", max_length=64),
        ),
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="bot_utm_campaign",
            field=models.CharField(blank=True, default="notifications", max_length=64),
        ),
    ]
