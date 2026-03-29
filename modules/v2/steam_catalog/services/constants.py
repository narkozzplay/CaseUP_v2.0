from __future__ import annotations

import re

from modules.v2.steam_catalog.models import SteamItem


EXTERIOR_LABELS = [
    "Factory New",
    "Minimal Wear",
    "Field-Tested",
    "Well-Worn",
    "Battle-Scarred",
]

EXTERIOR_ORDER = {
    "Factory New": 1,
    "Minimal Wear": 2,
    "Field-Tested": 3,
    "Well-Worn": 4,
    "Battle-Scarred": 5,
}

WEAR_RU = {
    "Factory New": "Прямо с завода",
    "Minimal Wear": "Немного поношенное",
    "Field-Tested": "После полевых испытаний",
    "Well-Worn": "Поношенное",
    "Battle-Scarred": "Закалённое в боях",
}

RARITY_RU_LABELS = {
    "consumer": "Ширпотреб",
    "industrial": "Промышленное качество",
    "milspec": "Армейское качество",
    "restricted": "Запрещённое",
    "classified": "Засекреченное",
    "covert": "Тайное",
    "contraband": "Контрабанда",
    "extraordinary": "Крайне редкий предмет",
}

_PRICE_RE = re.compile(r"[\d\.,]+")
_STATTRAK_PREFIXES = ("StatTrak™ ", "StatTrak ")
_EXTERIOR_RE = re.compile(r"\(([^()]*)\)\s*$")


__all__ = [
    "EXTERIOR_LABELS",
    "EXTERIOR_ORDER",
    "WEAR_RU",
    "RARITY_RU_LABELS",
    "_PRICE_RE",
    "_STATTRAK_PREFIXES",
    "_EXTERIOR_RE",
    "SteamItem",
]
