"""CaseUP-only profile effects.

Effects are pure CSS (see frontend/static/css/modules/profile_effects.css), so the
catalog lives in code: a key is stored on UserCustomization and turned into a class
name `pfx-<slot>--<key>` when the profile is rendered.
"""
from __future__ import annotations

from dataclasses import dataclass

SLOT_FRAME = "frame"
SLOT_NAME = "name"
SLOT_HERO = "hero"
SLOTS = (SLOT_FRAME, SLOT_NAME, SLOT_HERO)

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

    @property
    def css_class(self) -> str:
        return f"pfx-{self.slot}--{self.key}"

    @property
    def rarity_label_ru(self) -> str:
        return RARITY_LABELS[self.rarity][0]

    @property
    def rarity_label_en(self) -> str:
        return RARITY_LABELS[self.rarity][1]


EFFECTS: tuple[ProfileEffect, ...] = (
    # Avatar frames
    ProfileEffect(SLOT_FRAME, "radar", "Радар", "Radar", "restricted"),
    ProfileEffect(SLOT_FRAME, "neon", "Неон", "Neon", "classified"),
    ProfileEffect(SLOT_FRAME, "covert", "Тайное сияние", "Covert glow", "covert"),
    ProfileEffect(SLOT_FRAME, "inferno", "Инферно", "Inferno", "covert"),
    ProfileEffect(SLOT_FRAME, "gold", "Золотая лихорадка", "Gold rush", "contraband"),
    ProfileEffect(SLOT_FRAME, "holo", "Голограмма", "Hologram", "contraband"),
    # Nickname
    ProfileEffect(SLOT_NAME, "heat", "Раскалённый", "Heated", "restricted"),
    ProfileEffect(SLOT_NAME, "neon", "Неоновая вывеска", "Neon sign", "classified"),
    ProfileEffect(SLOT_NAME, "glitch", "Глитч", "Glitch", "covert"),
    ProfileEffect(SLOT_NAME, "gold", "Золото", "Gold", "contraband"),
    ProfileEffect(SLOT_NAME, "holo", "Голограмма", "Hologram", "contraband"),
    # Profile header
    ProfileEffect(SLOT_HERO, "rain", "Цифровой дождь", "Digital rain", "milspec"),
    ProfileEffect(SLOT_HERO, "scanlines", "Тактический монитор", "Tactical monitor", "restricted"),
    ProfileEffect(SLOT_HERO, "aurora", "Северное сияние", "Aurora", "classified"),
    ProfileEffect(SLOT_HERO, "embers", "Искры", "Embers", "covert"),
)

_BY_SLOT_KEY = {(effect.slot, effect.key): effect for effect in EFFECTS}


def get_effect(slot: str, key: str) -> ProfileEffect | None:
    return _BY_SLOT_KEY.get((str(slot or ""), str(key or "")))


def effects_for_slot(slot: str) -> list[ProfileEffect]:
    return [effect for effect in EFFECTS if effect.slot == slot]
