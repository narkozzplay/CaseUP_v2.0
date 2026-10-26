from django.db import migrations

OLD_URL = "https://t.me/caseup_pro_anons"
NEW_URL = "https://t.me/+Oo3Eb-Zc1n00Yzgy"


def use_invite_link(apps, schema_editor):
    # Пригласительная ссылка: Telegram считает по ней, сколько людей пришло с сайта.
    HouseBanner = apps.get_model("ads", "HouseBanner")
    HouseBanner.objects.filter(url=OLD_URL).update(url=NEW_URL)


def restore_channel_link(apps, schema_editor):
    HouseBanner = apps.get_model("ads", "HouseBanner")
    HouseBanner.objects.filter(url=NEW_URL).update(url=OLD_URL)


class Migration(migrations.Migration):
    dependencies = [
        ("ads", "0005_promo_banner_videos"),
    ]

    operations = [
        migrations.RunPython(use_invite_link, restore_channel_link),
    ]
