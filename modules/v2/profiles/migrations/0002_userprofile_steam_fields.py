from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="steam_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="steam_personaname",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="steam_avatar",
            field=models.URLField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="steam_profile_url",
            field=models.URLField(blank=True, default=""),
        ),
    ]