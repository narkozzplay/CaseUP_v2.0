from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class AdSettings(models.Model):
    class Provider(models.TextChoices):
        HOUSE = "house", "Свои баннеры"
        YANDEX = "yandex", "Рекламная сеть Яндекса (РСЯ)"
        GOOGLE = "google", "Google AdSense"
        CUSTOM = "custom", "Свой HTML-код"

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    is_enabled = models.BooleanField("Показывать рекламу", default=True)
    provider = models.CharField("Рекламная сеть", max_length=16, choices=Provider.choices, default=Provider.HOUSE)
    adsense_client_id = models.CharField(
        "AdSense client ID",
        max_length=64,
        blank=True,
        default="",
        help_text="Например, ca-pub-1234567890123456. Нужен только для Google AdSense.",
    )
    feed_every_n = models.PositiveSmallIntegerField(
        "Блок в ленте каталога через каждые N карточек",
        default=12,
        help_text="0 — не показывать блок внутри ленты.",
    )
    show_remove_link = models.BooleanField("Ссылка «Убрать рекламу» под блоком", default=True)
    show_to_staff = models.BooleanField(
        "Показывать администрации",
        default=False,
        help_text="Для проверки блоков. Обычно администрация рекламу не видит.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки рекламы"
        verbose_name_plural = "Настройки рекламы"

    def __str__(self) -> str:
        return "Настройки рекламы"

    def clean(self):
        self.adsense_client_id = (self.adsense_client_id or "").strip()
        if self.is_enabled and self.provider == self.Provider.GOOGLE and not self.adsense_client_id:
            raise ValidationError({"adsense_client_id": "Для AdSense нужен client ID."})

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.full_clean()
        return super().save(*args, **kwargs)


class AdPlacement(models.Model):
    key = models.CharField("Ключ", max_length=64, unique=True)
    name = models.CharField("Место", max_length=120)
    description = models.CharField("Где показывается", max_length=255, blank=True, default="")
    is_enabled = models.BooleanField("Включено", default=True)
    house_title = models.CharField("Заголовок баннера", max_length=120, blank=True, default="")
    house_text = models.CharField("Текст баннера", max_length=255, blank=True, default="")
    house_image = models.ImageField("Картинка баннера", upload_to="ads/", null=True, blank=True)
    house_url = models.CharField(
        "Ссылка",
        max_length=500,
        blank=True,
        default="",
        help_text="Куда ведёт баннер: адрес на сайте (/market/) или полный https://…",
    )
    house_button = models.CharField("Текст кнопки", max_length=40, blank=True, default="")
    block_id = models.CharField(
        "ID блока",
        max_length=128,
        blank=True,
        default="",
        help_text="РСЯ: R-A-1234567-1. AdSense: data-ad-slot (только цифры).",
    )
    custom_html = models.TextField(
        "Свой код",
        blank=True,
        default="",
        help_text="Для провайдера «Свой код»: HTML баннера. Выводится как есть, вставляйте только проверенный код.",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Рекламное место"
        verbose_name_plural = "Рекламные места"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.name

    @property
    def has_house_banner(self) -> bool:
        return bool(self.house_image or self.house_title.strip())

    def clean(self):
        url = (self.house_url or "").strip()
        if url and not (url.startswith("/") or url.startswith("https://") or url.startswith("http://")):
            raise ValidationError({"house_url": "Ссылка должна начинаться с / или https://."})

    def save(self, *args, **kwargs):
        self.key = (self.key or "").strip()
        self.house_url = (self.house_url or "").strip()
        self.block_id = (self.block_id or "").strip()
        return super().save(*args, **kwargs)
