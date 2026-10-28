"""CS2 Majors and the Pick'em challenge.

A Major has Swiss stages (pick 2 teams to go 3-0, 6 to advance with 3-1/3-2 and 2 to go 0-3) and a
single-elimination playoff stage where the whole bracket is filled before it starts. Picks lock when
their stage starts. Results are entered in the admin; rewards are granted by rules once the stages
they depend on are settled.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

ZERO = Decimal("0.00")

# How many teams a Swiss stage asks for in each slot, as in the in-game Pick'em.
SWISS_PICK_LIMITS = {"3-0": 2, "advance": 6, "0-3": 2}
# Prizes above this value are taxable (НДФЛ): the winner's full name and ИНН are collected first.
TAX_FREE_PRIZE_LIMIT = Decimal("4000")


class Team(models.Model):
    class Region(models.TextChoices):
        EUROPE = "europe", "Европа"
        CIS = "cis", "СНГ"
        AMERICAS = "americas", "Америка"
        ASIA = "asia", "Азия и Океания"

    name = models.CharField("Название", max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    tag = models.CharField("Короткое имя", max_length=16, blank=True, default="", help_text="Для узких мест: NAVI, VP, G2.")
    region = models.CharField("Регион", max_length=16, choices=Region.choices, blank=True, default="")
    logo = models.ImageField("Логотип", upload_to="majors/teams/", null=True, blank=True, help_text="Квадратный PNG/WebP на прозрачном фоне. Заменяет встроенный логотип.")
    logo_static = models.CharField("Встроенный логотип", max_length=200, blank=True, default="", help_text="Путь в static, например img/majors/teams/navi.webp.")
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Команда"
        verbose_name_plural = "Команды"

    def __str__(self) -> str:
        return self.name

    @property
    def short(self) -> str:
        return self.tag or self.name

    @property
    def logo_url(self) -> str:
        if self.logo:
            return self.logo.url
        if self.logo_static:
            return static(self.logo_static)
        return ""


class Major(models.Model):
    name = models.CharField("Название", max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    location = models.CharField("Место", max_length=120, blank=True, default="")
    venue = models.CharField("Арена плей-офф", max_length=120, blank=True, default="")
    starts_at = models.DateTimeField("Старт", help_text="Для таймера на странице.")
    ends_on = models.DateField("Финал")
    prize_pool = models.CharField("Призовой фонд", max_length=60, blank=True, default="")
    description = models.TextField("Описание", blank=True, default="")
    banner = models.ImageField("Баннер", upload_to="majors/banners/", null=True, blank=True, help_text="Шапка страницы: логотип мейджора + Pick'em, 1920×640 (3:1).")
    banner_video_mp4 = models.FileField(
        "Анимация баннера (MP4)", upload_to="majors/banners/", null=True, blank=True,
        validators=[FileExtensionValidator(["mp4"])],
        help_text="Необязательно. Петля 1920×640 без звука; картинка «Баннер» остаётся заставкой.",
    )
    banner_video_webm = models.FileField(
        "Анимация баннера (WebM)", upload_to="majors/banners/", null=True, blank=True,
        validators=[FileExtensionValidator(["webm"])],
        help_text="Необязательно, тот же ролик в WebM: он легче, браузер возьмёт его первым.",
    )
    is_published = models.BooleanField("Опубликован", default=False)
    require_steam = models.BooleanField("Pick'em только с привязанным Steam", default=True)
    items_keyword = models.CharField("Предметы: ключ в названии", max_length=60, blank=True, default="", help_text="Наклейки, капсулы и сувенирные наборы из каталога, в названии которых есть эти слова, например «Singapore 2026».")
    extra_items = models.ManyToManyField("steam_catalog.SteamItem", blank=True, related_name="+", verbose_name="Предметы вручную", help_text="Если предмет не нашёлся по ключу.")
    items_known_count = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-starts_at",)
        verbose_name = "Мейджор"
        verbose_name_plural = "Мейджоры"

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("majors:detail", args=[self.slug])

    @property
    def has_started(self) -> bool:
        return self.starts_at <= timezone.now()

    @property
    def is_finished(self) -> bool:
        return timezone.localdate() > self.ends_on


class Stage(models.Model):
    class Kind(models.TextChoices):
        SWISS = "swiss", "Швейцарская система"
        PLAYOFFS = "playoffs", "Плей-офф"

    major = models.ForeignKey(Major, on_delete=models.CASCADE, related_name="stages")
    order = models.PositiveSmallIntegerField("Порядок")
    name = models.CharField("Название", max_length=60)
    kind = models.CharField("Формат", max_length=16, choices=Kind.choices, default=Kind.SWISS)
    starts_at = models.DateTimeField("Начало", help_text="В этот момент прогнозы на стадию закрываются.")
    ends_on = models.DateField("Окончание", null=True, blank=True)
    pass_correct = models.PositiveSmallIntegerField("Верных прогнозов для прохождения", default=5, help_text="Как в игре: 5 из 10 для швейцарской стадии.")
    is_settled = models.BooleanField("Итоги подведены", default=False, help_text="Все результаты внесены; по стадии можно выдавать награды.")
    teams = models.ManyToManyField(Team, through="StageTeam", related_name="stages", blank=True)

    class Meta:
        ordering = ("major", "order")
        verbose_name = "Стадия"
        verbose_name_plural = "Стадии"
        constraints = [models.UniqueConstraint(fields=["major", "order"], name="majors_stage_order_unique")]

    def __str__(self) -> str:
        return f"{self.major.name} · {self.name}"

    @property
    def is_locked(self) -> bool:
        return self.starts_at <= timezone.now()

    @property
    def is_swiss(self) -> bool:
        return self.kind == self.Kind.SWISS


class StageTeam(models.Model):
    class Record(models.TextChoices):
        PENDING = "", "Играет"
        R30 = "3-0", "3-0"
        R31 = "3-1", "3-1"
        R32 = "3-2", "3-2"
        R23 = "2-3", "2-3"
        R13 = "1-3", "1-3"
        R03 = "0-3", "0-3"

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="entries")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="stage_entries")
    seed = models.PositiveSmallIntegerField("Посев", default=0)
    record = models.CharField("Итог", max_length=3, choices=Record.choices, blank=True, default="")

    class Meta:
        ordering = ("stage", "seed", "team__name")
        verbose_name = "Команда стадии"
        verbose_name_plural = "Команды стадии"
        constraints = [models.UniqueConstraint(fields=["stage", "team"], name="majors_stage_team_unique")]

    def __str__(self) -> str:
        return f"{self.team} ({self.record or 'играет'})"

    @property
    def outcome(self) -> str:
        """The Pick'em slot this record fills: 3-0, advance, 0-3, out, or '' while the team plays."""
        if self.record == self.Record.R30:
            return "3-0"
        if self.record in (self.Record.R31, self.Record.R32):
            return "advance"
        if self.record == self.Record.R03:
            return "0-3"
        if self.record:
            return "out"
        return ""

    @property
    def advanced(self) -> bool:
        return self.outcome in ("3-0", "advance")


class Match(models.Model):
    """A playoff match. Round 1 holds the quarterfinals; the winners of slots 2k-1 and 2k meet in slot k of the next round."""

    class Round(models.IntegerChoices):
        QUARTERFINAL = 1, "Четвертьфинал"
        SEMIFINAL = 2, "Полуфинал"
        FINAL = 3, "Финал"
        THIRD_PLACE = 4, "Матч за 3-е место"

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="matches")
    round = models.PositiveSmallIntegerField("Раунд", choices=Round.choices)
    slot = models.PositiveSmallIntegerField("Номер в раунде", default=1)
    team_a = models.ForeignKey(Team, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    team_b = models.ForeignKey(Team, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    winner = models.ForeignKey(Team, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    score = models.CharField("Счёт", max_length=20, blank=True, default="", help_text="Например 2:1.")
    starts_at = models.DateTimeField("Начало", null=True, blank=True)

    class Meta:
        ordering = ("stage", "round", "slot")
        verbose_name = "Матч плей-офф"
        verbose_name_plural = "Матчи плей-офф"
        constraints = [models.UniqueConstraint(fields=["stage", "round", "slot"], name="majors_match_slot_unique")]

    def __str__(self) -> str:
        teams = " vs ".join(team.short for team in (self.team_a, self.team_b) if team) or "TBD"
        return f"{self.get_round_display()} {self.slot}: {teams}"

    def clean(self):
        super().clean()
        if self.winner_id and self.winner_id not in (self.team_a_id, self.team_b_id):
            raise ValidationError({"winner": "Победитель должен быть одной из команд матча."})

    @property
    def loser(self):
        if not self.winner_id:
            return None
        return self.team_b if self.winner_id == self.team_a_id else self.team_a


class SwissPick(models.Model):
    class Pick(models.TextChoices):
        R30 = "3-0", "3-0"
        ADVANCE = "advance", "Проход"
        R03 = "0-3", "0-3"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="major_swiss_picks")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="swiss_picks")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    pick = models.CharField(max_length=8, choices=Pick.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Прогноз на стадию"
        verbose_name_plural = "Прогнозы на стадии"
        constraints = [models.UniqueConstraint(fields=["user", "stage", "team"], name="majors_swiss_pick_unique")]


class BracketPick(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="major_bracket_picks")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="bracket_picks")
    round = models.PositiveSmallIntegerField(choices=Match.Round.choices)
    slot = models.PositiveSmallIntegerField(default=1)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Прогноз на плей-офф"
        verbose_name_plural = "Прогнозы на плей-офф"
        constraints = [models.UniqueConstraint(fields=["user", "stage", "round", "slot"], name="majors_bracket_pick_unique")]


class RewardRule(models.Model):
    class Condition(models.TextChoices):
        ALL_STAGES = "all_stages", "Пройдены все стадии"
        PERFECT = "perfect", "Все прогнозы верны"
        STAGE_PASSED = "stage_passed", "Пройдена стадия"
        SEMIFINALISTS = "semifinalists", "Угаданы полуфиналисты"
        FINALISTS = "finalists", "Угаданы финалисты"
        CHAMPION = "champion", "Угадан чемпион"

    class Kind(models.TextChoices):
        BALANCE = "balance", "Бонус на баланс"
        SUBSCRIPTION = "subscription", "Дни подписки"
        ITEM = "item", "Скин"

    major = models.ForeignKey(Major, on_delete=models.CASCADE, related_name="reward_rules")
    title = models.CharField("Название", max_length=120)
    condition = models.CharField("Условие", max_length=20, choices=Condition.choices)
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, null=True, blank=True, related_name="reward_rules", help_text="Для «Пройдена стадия»: пусто — награда за каждую пройденную стадию.")
    min_correct = models.PositiveSmallIntegerField("Сколько верных нужно", null=True, blank=True, help_text="Полуфиналисты: из 4 четвертьфиналов; финалисты: из 2 полуфиналов; стадия: вместо порога стадии. Пусто — все.")
    kind = models.CharField("Награда", max_length=16, choices=Kind.choices, default=Kind.BALANCE)
    amount = models.DecimalField("Сумма, ₽", max_digits=10, decimal_places=2, default=ZERO)
    plan = models.ForeignKey("subscriptions.SubscriptionPlan", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="Тариф")
    days = models.PositiveSmallIntegerField("Дней подписки", default=0)
    item = models.ForeignKey("steam_catalog.SteamItem", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="Скин", help_text="Отправляется трейдом на трейд-ссылку игрока; такие награды всегда выдаются после проверки.")
    requires_approval = models.BooleanField("Выдавать после проверки", default=False, help_text="Награда ждёт одобрения в админке: защита от мультиаккаунтов на крупных призах.")
    is_active = models.BooleanField("Активно", default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("major", "sort_order", "id")
        verbose_name = "Награда Pick'em"
        verbose_name_plural = "Награды Pick'em"

    def __str__(self) -> str:
        return f"{self.title}: {self.reward_label}"

    def clean(self):
        super().clean()
        if self.kind == self.Kind.BALANCE and (self.amount is None or self.amount <= 0):
            raise ValidationError({"amount": "Укажите сумму бонуса."})
        if self.kind == self.Kind.SUBSCRIPTION and (self.plan_id is None or not self.days):
            raise ValidationError({"plan": "Укажите тариф и число дней."})
        if self.kind == self.Kind.ITEM and self.item_id is None:
            raise ValidationError({"item": "Выберите скин из каталога."})
        if self.stage_id and self.stage.major_id != self.major_id:
            raise ValidationError({"stage": "Стадия другого мейджора."})

    @property
    def prize_value(self) -> Decimal:
        if self.kind == self.Kind.ITEM:
            return (self.item.price_rub if self.item_id else None) or ZERO
        if self.kind == self.Kind.BALANCE:
            return self.amount or ZERO
        return ZERO

    @property
    def needs_tax_data(self) -> bool:
        """A prize above the НДФЛ-free limit: full name and ИНН are collected before it is handed over."""
        return self.prize_value > TAX_FREE_PRIZE_LIMIT

    @property
    def needs_approval(self) -> bool:
        return self.requires_approval or self.kind == self.Kind.ITEM

    @property
    def reward_label(self) -> str:
        if self.kind == self.Kind.ITEM:
            return self.item.market_hash_name if self.item_id else "Скин"
        if self.kind == self.Kind.SUBSCRIPTION:
            return f"{self.plan.name if self.plan_id else 'Подписка'} на {self.days} дн."
        amount = self.amount or ZERO
        return f"{amount:.0f} ₽" if amount == amount.to_integral() else f"{amount:.2f} ₽"


class RewardGrant(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт проверки"
        NEEDS_TRADE_URL = "needs_trade_url", "Ждёт трейд-ссылку"
        TO_SEND = "to_send", "Скин ждёт отправки"
        GRANTED = "granted", "Выдана"
        REJECTED = "rejected", "Отклонена"

    rule = models.ForeignKey(RewardRule, on_delete=models.PROTECT, related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="major_reward_grants")
    stage = models.ForeignKey(Stage, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # "" for one-off rules, "stage:<id>" for a rule paid once per stage: unique together with rule and user.
    scope = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    ledger_entry_id = models.BigIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Выданная награда"
        verbose_name_plural = "Выданные награды"
        constraints = [models.UniqueConstraint(fields=["rule", "user", "scope"], name="majors_grant_once")]

    def __str__(self) -> str:
        return f"{self.user} · {self.rule.title} · {self.get_status_display()}"
