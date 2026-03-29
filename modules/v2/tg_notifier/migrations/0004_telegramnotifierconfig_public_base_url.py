from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tg_notifier", "0003_rename_tg_notifier_user_id_42b40c_idx_tg_notifier_user_id_c31acd_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramnotifierconfig",
            name="public_base_url",
            field=models.URLField(blank=True, default="", max_length=255),
        ),
    ]