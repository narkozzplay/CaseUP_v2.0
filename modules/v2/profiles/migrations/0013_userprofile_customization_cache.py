from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0012_userprofile_uploaded_avatar"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="selected_avatar_url",
            field=models.URLField(blank=True, default="", help_text="Кешированный URL аватарки из каталога кастомизации"),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="selected_bg_static_url",
            field=models.URLField(blank=True, default="", help_text="Кешированный URL статичного фона из каталога"),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="selected_bg_video_url",
            field=models.URLField(blank=True, default="", help_text="Кешированный URL видео-фона из каталога"),
        ),
    ]
