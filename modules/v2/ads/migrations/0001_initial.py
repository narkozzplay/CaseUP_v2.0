from django.db import migrations, models


DEFAULT_PLACEMENTS = (
    ("catalog_top", "Каталог: над списком", "Баннер над карточками Steam-каталога.", 10),
    ("catalog_feed", "Каталог: в ленте", "Блок между карточками каталога, частота задаётся в настройках.", 20),
    ("item_below_chart", "Предмет: под графиком", "На странице предмета под графиком цены.", 30),
    ("sidebar_bottom", "Боковое меню: внизу", "Внизу бокового меню дашборда.", 40),
)


def seed_placements(apps, schema_editor):
    AdSettings = apps.get_model("ads", "AdSettings")
    AdPlacement = apps.get_model("ads", "AdPlacement")
    AdSettings.objects.get_or_create(singleton_key=1)
    for key, name, description, sort_order in DEFAULT_PLACEMENTS:
        AdPlacement.objects.get_or_create(
            key=key,
            defaults={"name": name, "description": description, "sort_order": sort_order},
        )


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AdSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("is_enabled", models.BooleanField(default=True, verbose_name="Показывать рекламу")),
                ("provider", models.CharField(choices=[("house", "Свои баннеры"), ("yandex", "Рекламная сеть Яндекса (РСЯ)"), ("google", "Google AdSense"), ("custom", "Свой HTML-код")], default="house", max_length=16, verbose_name="Рекламная сеть")),
                ("adsense_client_id", models.CharField(blank=True, default="", help_text="Например, ca-pub-1234567890123456. Нужен только для Google AdSense.", max_length=64, verbose_name="AdSense client ID")),
                ("feed_every_n", models.PositiveSmallIntegerField(default=12, help_text="0 — не показывать блок внутри ленты.", verbose_name="Блок в ленте каталога через каждые N карточек")),
                ("show_remove_link", models.BooleanField(default=True, verbose_name="Ссылка «Убрать рекламу» под блоком")),
                ("show_to_staff", models.BooleanField(default=False, help_text="Для проверки блоков. Обычно администрация рекламу не видит.", verbose_name="Показывать администрации")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Настройки рекламы", "verbose_name_plural": "Настройки рекламы"},
        ),
        migrations.CreateModel(
            name="AdPlacement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=64, unique=True, verbose_name="Ключ")),
                ("name", models.CharField(max_length=120, verbose_name="Место")),
                ("description", models.CharField(blank=True, default="", max_length=255, verbose_name="Где показывается")),
                ("is_enabled", models.BooleanField(default=True, verbose_name="Включено")),
                ("house_title", models.CharField(blank=True, default="", max_length=120, verbose_name="Заголовок баннера")),
                ("house_text", models.CharField(blank=True, default="", max_length=255, verbose_name="Текст баннера")),
                ("house_image", models.ImageField(blank=True, null=True, upload_to="ads/", verbose_name="Картинка баннера")),
                ("house_url", models.CharField(blank=True, default="", help_text="Куда ведёт баннер: адрес на сайте (/market/) или полный https://…", max_length=500, verbose_name="Ссылка")),
                ("house_button", models.CharField(blank=True, default="", max_length=40, verbose_name="Текст кнопки")),
                ("block_id", models.CharField(blank=True, default="", help_text="РСЯ: R-A-1234567-1. AdSense: data-ad-slot (только цифры).", max_length=128, verbose_name="ID блока")),
                ("custom_html", models.TextField(blank=True, default="", help_text="Для провайдера «Свой код»: HTML баннера. Выводится как есть, вставляйте только проверенный код.", verbose_name="Свой код")),
                ("sort_order", models.PositiveSmallIntegerField(default=100)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Рекламное место", "verbose_name_plural": "Рекламные места", "ordering": ("sort_order", "id")},
        ),
        migrations.RunPython(seed_placements, migrations.RunPython.noop),
    ]
