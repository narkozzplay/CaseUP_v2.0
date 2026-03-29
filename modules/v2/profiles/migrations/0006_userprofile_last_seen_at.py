from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0005_userprofile_steam_profile_background_video"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
