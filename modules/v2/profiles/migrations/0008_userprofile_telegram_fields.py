from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0007_userprofile_balance_non_negative"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="telegram_chat_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="telegram_first_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="telegram_last_interaction_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="telegram_linked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="telegram_username",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]