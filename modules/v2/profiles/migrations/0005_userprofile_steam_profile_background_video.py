from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0004_userprofile_steam_level_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="steam_profile_background_video",
            field=models.URLField(blank=True, default=""),
        ),
    ]