from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0003_userprofile_faceit_avatar_url_userprofile_faceit_elo_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="steam_level",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="steam_level_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]