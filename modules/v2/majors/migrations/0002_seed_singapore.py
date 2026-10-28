"""CS2 teams with built-in logos and PGL Major Singapore 2026 with its stages and default Pick'em rewards.

The stage dates are the announced plan (25 Nov – 13 Dec 2026, playoffs 10–13 Dec at the Singapore
Indoor Stadium); the exact lock times and the 32 teams are set in the admin once PGL publishes them.
"""
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.db import migrations

TEAMS = [
    # slug, name, tag, region
    ("vitality", "Team Vitality", "Vitality", "europe"),
    ("mouz", "MOUZ", "MOUZ", "europe"),
    ("spirit", "Team Spirit", "Spirit", "cis"),
    ("falcons", "Team Falcons", "Falcons", "europe"),
    ("the-mongolz", "The MongolZ", "MongolZ", "asia"),
    ("aurora", "Aurora", "Aurora", "europe"),
    ("navi", "Natus Vincere", "NAVI", "europe"),
    ("faze", "FaZe Clan", "FaZe", "europe"),
    ("g2", "G2 Esports", "G2", "europe"),
    ("furia", "FURIA", "FURIA", "americas"),
    ("liquid", "Team Liquid", "Liquid", "americas"),
    ("astralis", "Astralis", "Astralis", "europe"),
    ("heroic", "HEROIC", "HEROIC", "europe"),
    ("pain", "paiN Gaming", "paiN", "americas"),
    ("mibr", "MIBR", "MIBR", "americas"),
    ("3dmax", "3DMAX", "3DMAX", "europe"),
    ("virtus-pro", "Virtus.pro", "VP", "cis"),
    ("big", "BIG", "BIG", "europe"),
    ("gamerlegion", "GamerLegion", "GL", "europe"),
    ("b8", "B8", "B8", "cis"),
    ("betboom", "BetBoom Team", "BetBoom", "cis"),
    ("parivision", "PARIVISION", "PARI", "cis"),
    ("nemiga", "Nemiga", "Nemiga", "cis"),
    ("tyloo", "TYLOO", "TYLOO", "asia"),
    ("lynn-vision", "Lynn Vision", "Lynn", "asia"),
    ("rare-atom", "Rare Atom", "RA", "asia"),
    ("flyquest", "FlyQuest", "FlyQuest", "asia"),
    ("imperial", "Imperial Esports", "Imperial", "americas"),
    ("nrg", "NRG", "NRG", "americas"),
    ("m80", "M80", "M80", "americas"),
    ("wildcard", "Wildcard", "WC", "americas"),
    ("legacy", "Legacy", "Legacy", "americas"),
    ("fluxo", "Fluxo", "Fluxo", "americas"),
    ("9z", "9z Team", "9z", "americas"),
    ("red-canids", "RED Canids", "RED", "americas"),
    ("sharks", "Sharks Esports", "Sharks", "americas"),
    ("complexity", "Complexity Gaming", "coL", "americas"),
    ("cloud9", "Cloud9", "C9", "europe"),
    ("fnatic", "Fnatic", "fnatic", "europe"),
    ("nip", "Ninjas in Pyjamas", "NiP", "europe"),
    ("ence", "ENCE", "ENCE", "europe"),
    ("eternal-fire", "Eternal Fire", "EF", "europe"),
    ("monte", "Monte", "Monte", "europe"),
    ("sinners", "SINNERS Esports", "SINNERS", "europe"),
    ("og", "OG", "OG", "europe"),
    ("passion-ua", "Passion UA", "PUA", "europe"),
    ("saw", "SAW", "SAW", "europe"),
    ("apeks", "Apeks", "Apeks", "europe"),
    ("bestia", "BESTIA", "BESTIA", "americas"),
    ("koi", "KOI", "KOI", "europe"),
    ("metizport", "Metizport", "Metiz", "europe"),
    ("forze", "forZe", "forZe", "cis"),
]


def _utc(y, m, d, h=3):
    # 11:00 in Singapore.
    return datetime(y, m, d, h, 0, tzinfo=dt_timezone.utc)


def seed(apps, schema_editor):
    Team = apps.get_model("majors", "Team")
    Major = apps.get_model("majors", "Major")
    Stage = apps.get_model("majors", "Stage")
    Match = apps.get_model("majors", "Match")
    RewardRule = apps.get_model("majors", "RewardRule")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")

    for slug, name, tag, region in TEAMS:
        Team.objects.get_or_create(slug=slug, defaults={"name": name, "tag": tag, "region": region, "logo_static": f"img/majors/teams/{slug}.webp"})

    major, created = Major.objects.get_or_create(
        slug="singapore-2026",
        defaults={
            "name": "PGL Major Singapore 2026",
            "location": "Сингапур",
            "venue": "Singapore Indoor Stadium",
            "starts_at": _utc(2026, 11, 25),
            "ends_on": date(2026, 12, 13),
            "prize_pool": "$1 250 000",
            "items_keyword": "Singapore 2026",
            "description": "Первый мейджор CS2 в Юго-Восточной Азии: 32 команды, три швейцарские стадии и плей-офф на Singapore Indoor Stadium с финалом до трёх побед.",
            "is_published": True,
        },
    )
    if not created:
        return
    stages = [
        (1, "Стадия 1", "swiss", _utc(2026, 11, 25), date(2026, 11, 28)),
        (2, "Стадия 2", "swiss", _utc(2026, 11, 29), date(2026, 12, 2)),
        (3, "Стадия 3", "swiss", _utc(2026, 12, 3), date(2026, 12, 6)),
        (4, "Плей-офф", "playoffs", _utc(2026, 12, 10), date(2026, 12, 13)),
    ]
    playoffs = None
    for order, name, kind, starts_at, ends_on in stages:
        stage = Stage.objects.create(major=major, order=order, name=name, kind=kind, starts_at=starts_at, ends_on=ends_on, pass_correct=5)
        if kind == "playoffs":
            playoffs = stage
    for round_no, slots in ((1, 4), (2, 2), (3, 1), (4, 1)):
        for slot in range(1, slots + 1):
            Match.objects.create(stage=playoffs, round=round_no, slot=slot)

    plus = SubscriptionPlan.objects.filter(code="site_plus").first()
    pro = SubscriptionPlan.objects.filter(code="site_pro").first()
    rules = [
        dict(sort_order=10, title="Стадия пройдена: 5+ верных прогнозов", condition="stage_passed", kind="balance", amount=Decimal("25")),
        dict(sort_order=20, title="Угаданы полуфиналисты: 3 из 4", condition="semifinalists", min_correct=3, kind="balance", amount=Decimal("50")),
        dict(sort_order=30, title="Угаданы оба финалиста", condition="finalists", min_correct=2, kind="balance", amount=Decimal("100")),
        dict(sort_order=40, title="Угадан чемпион", condition="champion", kind="balance", amount=Decimal("150")),
    ]
    if plus is not None:
        rules.append(dict(sort_order=50, title="Пройдены все стадии", condition="all_stages", kind="subscription", plan=plus, days=30, requires_approval=True))
    if pro is not None:
        rules.append(dict(sort_order=60, title="Идеальный Pick'em: все прогнозы верны", condition="perfect", kind="subscription", plan=pro, days=90, requires_approval=True))
    for rule in rules:
        RewardRule.objects.create(major=major, **rule)


class Migration(migrations.Migration):
    dependencies = [
        ("majors", "0001_initial"),
        ("subscriptions", "0011_catalog_open_to_guests"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
