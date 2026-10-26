
import django.core.validators
from django.db import migrations, models


PROMO_BANNERS = (
    {
        "name": "Донат проекту",
        "title": "Поддержи CaseUP",
        "text": "Сервис живёт на донатах: каждый рубль идёт на серверы, обновление цен и новые функции.",
        "url": "/donate/",
        "button": "Поддержать",
        "weight": 10,
    },
    {
        "name": "Telegram-канал",
        "title": "CaseUP в Telegram",
        "text": "Рост и падение цен скинов CS2 каждые несколько часов — подпишись на канал.",
        "url": "https://t.me/caseup_pro_anons",
        "button": "Подписаться",
        "weight": 10,
    },
    {
        "name": "Тариф «Без рекламы»",
        "title": "Без рекламы за 99 ₽ в месяц",
        "text": "Чистый каталог и графики без баннеров. Подписка помогает развивать CaseUP.",
        "url": "/subscriptions/",
        "button": "Подключить",
        "weight": 10,
    },
)


def move_house_banners(apps, schema_editor):
    AdPlacement = apps.get_model("ads", "AdPlacement")
    HouseBanner = apps.get_model("ads", "HouseBanner")
    # Banners filled in per placement before this migration keep showing in their own place.
    for placement in AdPlacement.objects.all():
        title = (placement.house_title or "").strip()
        if not (title or placement.house_image):
            continue
        banner = HouseBanner.objects.create(
            name=title or placement.name,
            title=title,
            text=placement.house_text or "",
            image=placement.house_image.name if placement.house_image else None,
            url=(placement.house_url or "").strip() or "/subscriptions/",
            button=placement.house_button or "",
        )
        banner.placements.add(placement)
    for promo in PROMO_BANNERS:
        if not HouseBanner.objects.filter(url=promo["url"]).exists():
            HouseBanner.objects.create(**promo)


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="HouseBanner",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Видно только в админке.",
                        max_length=120,
                        verbose_name="Название",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True, default="", max_length=120, verbose_name="Заголовок"
                    ),
                ),
                (
                    "text",
                    models.CharField(
                        blank=True, default="", max_length=255, verbose_name="Текст"
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True, null=True, upload_to="ads/", verbose_name="Картинка"
                    ),
                ),
                (
                    "url",
                    models.CharField(
                        help_text="Адрес на сайте (/donate/) или полный https://…",
                        max_length=500,
                        verbose_name="Ссылка",
                    ),
                ),
                (
                    "button",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=40,
                        verbose_name="Текст кнопки",
                    ),
                ),
                (
                    "audience",
                    models.CharField(
                        choices=[
                            ("all", "Всем"),
                            ("guests", "Только гостям"),
                            ("users", "Только вошедшим"),
                        ],
                        default="all",
                        max_length=8,
                        verbose_name="Кому",
                    ),
                ),
                (
                    "show_to_ad_free",
                    models.BooleanField(
                        default=False,
                        help_text="Для подписчиков блок выводится с подписью «CaseUP» вместо «Реклама» и без ссылки «Убрать рекламу».",
                        verbose_name="Показывать и тем, кто отключил рекламу",
                    ),
                ),
                (
                    "weight",
                    models.PositiveSmallIntegerField(
                        default=10,
                        help_text="Чем больше, тем чаще баннер выпадает среди остальных в том же месте.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(100),
                        ],
                        verbose_name="Вес",
                    ),
                ),
                (
                    "starts_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Показывать с"
                    ),
                ),
                (
                    "ends_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Показывать до"
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(default=True, verbose_name="Включён"),
                ),
                (
                    "impressions",
                    models.PositiveIntegerField(
                        default=0, editable=False, verbose_name="Показы"
                    ),
                ),
                (
                    "clicks",
                    models.PositiveIntegerField(
                        default=0, editable=False, verbose_name="Клики"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "placements",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Пусто — во всех местах.",
                        related_name="house_banners",
                        to="ads.adplacement",
                        verbose_name="Места",
                    ),
                ),
            ],
            options={
                "verbose_name": "Свой баннер",
                "verbose_name_plural": "Свои баннеры",
                "ordering": ("-is_enabled", "-weight", "id"),
            },
        ),
        migrations.RunPython(move_house_banners, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="adplacement",
            name="house_button",
        ),
        migrations.RemoveField(
            model_name="adplacement",
            name="house_image",
        ),
        migrations.RemoveField(
            model_name="adplacement",
            name="house_text",
        ),
        migrations.RemoveField(
            model_name="adplacement",
            name="house_title",
        ),
        migrations.RemoveField(
            model_name="adplacement",
            name="house_url",
        ),
    ]
