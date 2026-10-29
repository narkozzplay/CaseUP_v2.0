"""CaseUP-only profile effects.

Effects are pure CSS (see frontend/static/css/modules/profile_effects.css), so the
catalog lives in code: a key is stored on UserCustomization and turned into a class
name `pfx-<slot>--<key>` when the profile is rendered.

Every effect has a source that says who may wear it:
- free: every signed-in user;
- pro: the profile_customization plan feature (Pro), staff and store authors;
- reward: only those who earned it (an achievement, a Pick'em prize, an admin grant).
  Rewards are cosmetic, bound to the account and cannot be sold or passed on.
"""
from __future__ import annotations

from dataclasses import dataclass

SLOT_FRAME = "frame"
SLOT_NAME = "name"
SLOT_HERO = "hero"
SLOT_TITLE = "title"
SLOT_ACCENT = "accent"
SLOTS = (SLOT_FRAME, SLOT_NAME, SLOT_HERO, SLOT_TITLE, SLOT_ACCENT)

SLOT_LABELS = {
    SLOT_FRAME: ("Рамка аватарки", "Avatar frame"),
    SLOT_NAME: ("Ник", "Nickname"),
    SLOT_HERO: ("Шапка профиля", "Profile header"),
    SLOT_TITLE: ("Титул под ником", "Title"),
    SLOT_ACCENT: ("Цвет акцента", "Accent color"),
}

SOURCE_FREE = "free"
SOURCE_PRO = "pro"
SOURCE_REWARD = "reward"

# CS2 rarity grades, used for the colored tag on each effect tile.
RARITY_LABELS = {
    "milspec": ("Армейское", "Mil-Spec"),
    "restricted": ("Запрещённое", "Restricted"),
    "classified": ("Засекреченное", "Classified"),
    "covert": ("Тайное", "Covert"),
    "contraband": ("Контрабанда", "Contraband"),
}


@dataclass(frozen=True)
class ProfileEffect:
    slot: str
    key: str
    title_ru: str
    title_en: str
    rarity: str
    source: str = SOURCE_PRO
    # For rewards: how to earn it, shown on the locked tile.
    how_ru: str = ""
    how_en: str = ""

    @property
    def css_class(self) -> str:
        return f"pfx-{self.slot}--{self.key}"

    @property
    def ref(self) -> str:
        return f"{self.slot}:{self.key}"

    @property
    def rarity_label_ru(self) -> str:
        return RARITY_LABELS[self.rarity][0]

    @property
    def rarity_label_en(self) -> str:
        return RARITY_LABELS[self.rarity][1]

    @property
    def is_free(self) -> bool:
        return self.source == SOURCE_FREE

    @property
    def is_pro(self) -> bool:
        return self.source == SOURCE_PRO

    @property
    def is_reward(self) -> bool:
        return self.source == SOURCE_REWARD


_PICKEM_RU = "Награда Pick'em: выдаётся за прогнозы на мейджор"
_PICKEM_EN = "Pick'em prize: earned with Major predictions"

EFFECTS: tuple[ProfileEffect, ...] = (
    # Avatar frames
    ProfileEffect(SLOT_FRAME, "steel", "Армейская сталь", "Army steel", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_FRAME, "radar", "Радар", "Radar", "restricted"),
    ProfileEffect(SLOT_FRAME, "neon", "Неон", "Neon", "classified"),
    ProfileEffect(SLOT_FRAME, "asiimov", "Азимов", "Asiimov", "classified"),
    ProfileEffect(SLOT_FRAME, "covert", "Тайное сияние", "Covert glow", "covert"),
    ProfileEffect(SLOT_FRAME, "inferno", "Инферно", "Inferno", "covert"),
    ProfileEffect(SLOT_FRAME, "fade", "Градиент", "Fade", "covert"),
    ProfileEffect(SLOT_FRAME, "doppler", "Доплер", "Doppler", "covert"),
    ProfileEffect(SLOT_FRAME, "gold", "Золотая лихорадка", "Gold rush", "contraband"),
    ProfileEffect(SLOT_FRAME, "holo", "Голограмма", "Hologram", "contraband"),
    ProfileEffect(
        SLOT_FRAME, "case_hardened", "Поверхностная закалка", "Case Hardened", "covert", SOURCE_REWARD,
        "Серия ежедневного бонуса 30 дней", "A 30-day daily bonus streak",
    ),
    ProfileEffect(SLOT_FRAME, "champion", "Чемпион мейджора", "Major champion", "contraband", SOURCE_REWARD, _PICKEM_RU, _PICKEM_EN),
    # Nickname
    ProfileEffect(SLOT_NAME, "milspec", "Армейский", "Army", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_NAME, "heat", "Раскалённый", "Heated", "restricted"),
    ProfileEffect(SLOT_NAME, "neon", "Неоновая вывеска", "Neon sign", "classified"),
    ProfileEffect(SLOT_NAME, "asiimov", "Азимов", "Asiimov", "classified"),
    ProfileEffect(SLOT_NAME, "glitch", "Глитч", "Glitch", "covert"),
    ProfileEffect(SLOT_NAME, "fade", "Градиент", "Fade", "covert"),
    ProfileEffect(SLOT_NAME, "doppler", "Доплер", "Doppler", "covert"),
    ProfileEffect(SLOT_NAME, "gold", "Золото", "Gold", "contraband"),
    ProfileEffect(SLOT_NAME, "holo", "Голограмма", "Hologram", "contraband"),
    ProfileEffect(
        SLOT_NAME, "case_hardened", "Поверхностная закалка", "Case Hardened", "covert", SOURCE_REWARD,
        "10 завершённых сделок на рынке", "10 completed Market deals",
    ),
    ProfileEffect(SLOT_NAME, "prophet", "Пророк", "Prophet", "contraband", SOURCE_REWARD, _PICKEM_RU, _PICKEM_EN),
    # Profile header
    ProfileEffect(SLOT_HERO, "rain", "Цифровой дождь", "Digital rain", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_HERO, "scanlines", "Тактический монитор", "Tactical monitor", "restricted"),
    ProfileEffect(SLOT_HERO, "smoke", "Дымовая", "Smoke", "classified"),
    ProfileEffect(SLOT_HERO, "aurora", "Северное сияние", "Aurora", "classified"),
    ProfileEffect(SLOT_HERO, "embers", "Искры", "Embers", "covert"),
    ProfileEffect(
        SLOT_HERO, "dust2", "Dust II", "Dust II", "covert", SOURCE_REWARD,
        "3 приглашённых друга", "3 invited friends",
    ),
    ProfileEffect(SLOT_HERO, "confetti", "Трофей", "Trophy", "contraband", SOURCE_REWARD, _PICKEM_RU, _PICKEM_EN),
    # Title under the nickname: the title text is the effect title
    ProfileEffect(SLOT_TITLE, "cs2_player", "Игрок CS2", "CS2 player", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_TITLE, "skin_fan", "Ценитель скинов", "Skin lover", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_TITLE, "pro_trader", "Pro-трейдер", "Pro trader", "classified"),
    ProfileEffect(SLOT_TITLE, "market_analyst", "Аналитик рынка", "Market analyst", "classified"),
    ProfileEffect(
        SLOT_TITLE, "price_hunter", "Охотник за ценами", "Price hunter", "restricted", SOURCE_REWARD,
        "3 уведомления о цене", "3 price alerts",
    ),
    ProfileEffect(
        SLOT_TITLE, "collector", "Коллекционер", "Collector", "restricted", SOURCE_REWARD,
        "10 предметов в избранном каталога", "10 favorites in the catalog",
    ),
    ProfileEffect(
        SLOT_TITLE, "investor", "Инвестор", "Investor", "classified", SOURCE_REWARD,
        "5 покупок в разделе «Инвестиции»", "5 purchases in Investments",
    ),
    ProfileEffect(
        SLOT_TITLE, "trader", "Трейдер", "Trader", "restricted", SOURCE_REWARD,
        "Первая завершённая сделка на рынке", "First completed Market deal",
    ),
    ProfileEffect(
        SLOT_TITLE, "regular", "Завсегдатай", "Regular", "restricted", SOURCE_REWARD,
        "Серия ежедневного бонуса 7 дней", "A 7-day daily bonus streak",
    ),
    ProfileEffect(
        SLOT_TITLE, "recruiter", "Вербовщик", "Recruiter", "classified", SOURCE_REWARD,
        "Приглашённый друг пополнил баланс", "An invited friend topped up",
    ),
    ProfileEffect(
        SLOT_TITLE, "veteran", "Ветеран CaseUP", "CaseUP veteran", "covert", SOURCE_REWARD,
        "Год на CaseUP", "A year on CaseUP",
    ),
    ProfileEffect(SLOT_TITLE, "pickem_player", "Участник Pick'em", "Pick'em player", "restricted", SOURCE_REWARD, _PICKEM_RU, _PICKEM_EN),
    ProfileEffect(SLOT_TITLE, "oracle", "Оракул мейджора", "Major oracle", "contraband", SOURCE_REWARD, _PICKEM_RU, _PICKEM_EN),
    # Accent color of the profile page
    ProfileEffect(SLOT_ACCENT, "blue", "Синий", "Blue", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_ACCENT, "green", "Зелёный", "Green", "milspec", SOURCE_FREE),
    ProfileEffect(SLOT_ACCENT, "purple", "Фиолетовый", "Purple", "restricted", SOURCE_FREE),
    ProfileEffect(SLOT_ACCENT, "pink", "Розовый", "Pink", "classified"),
    ProfileEffect(SLOT_ACCENT, "cyan", "Бирюзовый", "Cyan", "classified"),
    ProfileEffect(SLOT_ACCENT, "red", "Красный", "Red", "covert"),
    ProfileEffect(SLOT_ACCENT, "gold", "Золотой", "Gold", "contraband"),
)

_BY_SLOT_KEY = {(effect.slot, effect.key): effect for effect in EFFECTS}


def get_effect(slot: str, key: str) -> ProfileEffect | None:
    return _BY_SLOT_KEY.get((str(slot or ""), str(key or "")))


def get_effect_by_ref(ref: str) -> ProfileEffect | None:
    slot, _, key = str(ref or "").partition(":")
    return get_effect(slot, key)


def effects_for_slot(slot: str) -> list[ProfileEffect]:
    return [effect for effect in EFFECTS if effect.slot == slot]


def reward_choices() -> list[tuple[str, str]]:
    """(ref, label) of every reward effect, for admin forms."""
    return [
        (effect.ref, f"{SLOT_LABELS[effect.slot][0]}: {effect.title_ru}")
        for effect in EFFECTS
        if effect.is_reward
    ]


@dataclass(frozen=True)
class EffectKit:
    """A ready look: one click puts several effects on at once."""

    key: str
    title_ru: str
    title_en: str
    parts: dict

    @property
    def effects(self) -> list[ProfileEffect]:
        return [effect for slot, key in self.parts.items() if (effect := get_effect(slot, key))]


KITS: tuple[EffectKit, ...] = (
    EffectKit("recruit", "Новобранец", "Recruit", {SLOT_FRAME: "steel", SLOT_NAME: "milspec", SLOT_HERO: "rain", SLOT_ACCENT: "blue"}),
    EffectKit("tactical", "Тактик", "Tactician", {SLOT_FRAME: "radar", SLOT_NAME: "glitch", SLOT_HERO: "scanlines", SLOT_ACCENT: "green"}),
    EffectKit("cyber", "Киберпанк", "Cyberpunk", {SLOT_FRAME: "neon", SLOT_NAME: "neon", SLOT_HERO: "aurora", SLOT_ACCENT: "cyan"}),
    EffectKit("fire", "Инферно", "Inferno", {SLOT_FRAME: "inferno", SLOT_NAME: "heat", SLOT_HERO: "embers", SLOT_ACCENT: "red"}),
    EffectKit("knife", "Нож в инвентаре", "Knife in the inventory", {SLOT_FRAME: "fade", SLOT_NAME: "doppler", SLOT_HERO: "smoke", SLOT_ACCENT: "pink"}),
    EffectKit("gold", "Контрабанда", "Contraband", {SLOT_FRAME: "gold", SLOT_NAME: "gold", SLOT_HERO: "embers", SLOT_ACCENT: "gold"}),
    EffectKit("major", "Чемпион мейджора", "Major champion", {SLOT_FRAME: "champion", SLOT_NAME: "prophet", SLOT_HERO: "confetti", SLOT_TITLE: "oracle", SLOT_ACCENT: "gold"}),
)

_KITS_BY_KEY = {kit.key: kit for kit in KITS}


def get_kit(key: str) -> EffectKit | None:
    return _KITS_BY_KEY.get(str(key or ""))
