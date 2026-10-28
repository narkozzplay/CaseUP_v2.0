from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tg_notifier", "0010_channel_digest_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="admin_signup_notices",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="admin_topup_notices",
            field=models.BooleanField(default=True),
        ),
    ]
