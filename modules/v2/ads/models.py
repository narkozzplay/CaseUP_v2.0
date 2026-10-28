from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
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

    def save(self, *args, **kwargs):
        self.key = (self.key or "").strip()
        self.block_id = (self.block_id or "").strip()
        return super().save(*args, **kwargs)


MAX_VIDEO_BYTES = 3 * 1024 * 1024


class HouseBanner(models.Model):
    """CaseUP's own promo (donations, the Telegram channel, the ad-free plan). Several banners rotate in one place."""

    class Audience(models.TextChoices):
        ALL = "all", "Всем"
        GUESTS = "guests", "Только гостям"
        USERS = "users", "Только вошедшим"

    name = models.CharField("Название", max_length=120, help_text="Видно только в админке.")
    title = models.CharField("Заголовок", max_length=120, blank=True, default="")
    text = models.CharField("Текст", max_length=255, blank=True, default="")
    image = models.ImageField("Картинка", upload_to="ads/", null=True, blank=True)
    video = models.FileField(
        "Видео",
        upload_to="ads/video/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["mp4", "webm"])],
        help_text="Необязательно. Короткая петля MP4/WebM до 3 МБ, играет без звука по кругу; картинка остаётся заставкой.",
    )
    url = models.CharField(
        "Ссылка",
        max_length=500,
        help_text="Адрес на сайте (/donate/) или полный https://…",
    )
    button = models.CharField("Текст кнопки", max_length=40, blank=True, default="")
    placements = models.ManyToManyField(
        AdPlacement,
        blank=True,
        related_name="house_banners",
        verbose_name="Места",
        help_text="Пусто — во всех местах.",
    )
    audience = models.CharField("Кому", max_length=8, choices=Audience.choices, default=Audience.ALL)
    show_to_ad_free = models.BooleanField(
        "Показывать и тем, кто отключил рекламу",
        default=False,
        help_text="Для подписчиков блок выводится с подписью «CaseUP» вместо «Реклама» и без ссылки «Убрать рекламу».",
    )
    weight = models.PositiveSmallIntegerField(
        "Вес",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Чем больше, тем чаще баннер выпадает среди остальных в том же месте.",
    )
    starts_at = models.DateTimeField("Показывать с", null=True, blank=True)
    ends_at = models.DateTimeField("Показывать до", null=True, blank=True)
    is_enabled = models.BooleanField("Включён", default=True)
    impressions = models.PositiveIntegerField("Показы", default=0, editable=False)
    clicks = models.PositiveIntegerField("Клики", default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Свой баннер"
        verbose_name_plural = "Свои баннеры"
        ordering = ("-is_enabled", "-weight", "id")

    def __str__(self) -> str:
        return self.name

    @property
    def video_type(self) -> str:
        return "video/webm" if self.video and self.video.name.lower().endswith(".webm") else "video/mp4"

    @property
    def is_external(self) -> bool:
        return self.url.startswith("http")

    @property
    def ctr(self) -> float:
        return round(self.clicks * 100 / self.impressions, 2) if self.impressions else 0.0

    def clean(self):
        self.url = (self.url or "").strip()
        if self.url and not (self.url.startswith("/") or self.url.startswith("https://") or self.url.startswith("http://")):
            raise ValidationError({"url": "Ссылка должна начинаться с / или https://."})
        if self.url.startswith("//"):
            raise ValidationError({"url": "Ссылка должна начинаться с / или https://."})
        if not (self.image or (self.title or "").strip()):
            raise ValidationError({"title": "Нужен заголовок или картинка."})
        if self.video and not self.image:
            raise ValidationError({"image": "К видео нужна картинка: она показывается, пока видео грузится или если движение отключено."})
        if self.video and getattr(self.video, "size", 0) > MAX_VIDEO_BYTES:
            raise ValidationError({"video": "Видео больше 3 МБ — сожмите его."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "Окончание должно быть позже начала."})

    def save(self, *args, **kwargs):
        self.url = (self.url or "").strip()
        return super().save(*args, **kwargs)


class InfoBanner(models.Model):
    """A page banner about CaseUP itself (the Major Pick'em, stats, the market). Not an ad: no label, shown to everyone.

    The picture is wide art (4:1) with the subject on the right; title, text and button are real text over its left side.
    """

    class Slot(models.TextChoices):
        DASHBOARD = "dashboard", "Панель пользователя"
        HOME = "home", "Главная"
        STATS_TOP = "stats_top", "CS2 статистика: сверху"
        STATS_FACEIT = "stats_faceit", "CS2 статистика: перед FACEIT"
        DONATE_TOP = "donate_top", "Поддержать проект: сверху"
        DONATE_BOTTOM = "donate_bottom", "Поддержать проект: под формой"
        MARKET = "market", "Рынок"
        INVESTMENTS = "investments", "Инвестиции"
        CASES = "cases", "Кейсы"
        CUSTOMIZATION = "customization", "Оформление профиля"

    class Audience(models.TextChoices):
        ALL = "all", "Всем"
        GUESTS = "guests", "Только гостям"
        USERS = "users", "Только вошедшим"

    slot = models.CharField("Место", max_length=24, choices=Slot.choices, db_index=True)
    name = models.CharField("Название", max_length=120, help_text="Видно только в админке.")
    eyebrow = models.CharField("Надзаголовок", max_length=60, blank=True, default="")
    title = models.CharField("Заголовок", max_length=120)
    text = models.CharField("Текст", max_length=255, blank=True, default="")
    title_en = models.CharField("Заголовок (EN)", max_length=120, blank=True, default="")
    text_en = models.CharField("Текст (EN)", max_length=255, blank=True, default="")
    button = models.CharField("Кнопка", max_length=40, blank=True, default="")
    button_en = models.CharField("Кнопка (EN)", max_length=40, blank=True, default="")
    url = models.CharField("Ссылка", max_length=500, blank=True, default="", help_text="Адрес на сайте (/market/) или https://…; пусто — баннер без ссылки.")
    image = models.ImageField("Картинка", upload_to="info_banners/", null=True, blank=True, help_text="4:1, например 1536×384: объект справа, слева тёмный фон под текст.")
    video = models.FileField(
        "Видео",
        upload_to="info_banners/video/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["mp4", "webm"])],
        help_text="Необязательно. Петля MP4/WebM до 3 МБ без звука; картинка остаётся заставкой.",
    )
    icon = models.CharField("Иконка без картинки", max_length=32, blank=True, default="", help_text="Иконка сайта, пока картинки нет: market, steam, donations…")
    audience = models.CharField("Кому", max_length=8, choices=Audience.choices, default=Audience.ALL)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)
    starts_at = models.DateTimeField("Показывать с", null=True, blank=True)
    ends_at = models.DateTimeField("Показывать до", null=True, blank=True)
    is_enabled = models.BooleanField("Включён", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Инфо-баннер"
        verbose_name_plural = "Инфо-баннеры"
        ordering = ("slot", "sort_order", "id")

    def __str__(self) -> str:
        return self.name

    @property
    def video_type(self) -> str:
        return "video/webm" if self.video and self.video.name.lower().endswith(".webm") else "video/mp4"

    @property
    def is_external(self) -> bool:
        return self.url.startswith("http")

    def clean(self):
        self.url = (self.url or "").strip()
        if self.url and (self.url.startswith("//") or not (self.url.startswith("/") or self.url.startswith("https://") or self.url.startswith("http://"))):
            raise ValidationError({"url": "Ссылка должна начинаться с / или https://."})
        if self.video and not self.image:
            raise ValidationError({"image": "К видео нужна картинка: она показывается, пока видео грузится или если движение отключено."})
        if self.video and getattr(self.video, "size", 0) > MAX_VIDEO_BYTES:
            raise ValidationError({"video": "Видео больше 3 МБ — сожмите его."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "Окончание должно быть позже начала."})
