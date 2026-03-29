from django.db import migrations



def seed_public_server(apps, schema_editor):
    GameServer = apps.get_model("game_servers", "GameServer")
    GameServerPrivilege = apps.get_model("game_servers", "GameServerPrivilege")

    server, _created = GameServer.objects.get_or_create(
        slug="public",
        defaults={
            "name": "CS2 Public",
            "game": "cs2",
            "host": "127.0.0.1",
            "port": 27015,
            "server_path": r"W:\CaseUP_public",
            "description": "Локальный public-сервер Counter-Strike 2 для MVP-интеграции с CaseUP.",
            "shop_label": "CaseUP Store",
            "shop_description": "Здесь появятся игровые наборы, VIP и админ-привилегии.",
            "sync_notes": "Foundation для будущей синхронизации сайт ↔ сервер, SteamID и выдачи прав.",
            "is_enabled": True,
            "sort_order": 10,
        },
    )

    for defaults in [
        {
            "code": "vip-start",
            "name": "VIP Start",
            "privilege_type": "vip",
            "price": "299.00",
            "duration_days": 30,
            "description": "Базовая VIP-заготовка для будущего магазина.",
            "sort_order": 10,
            "metadata": {"placeholder": True},
        },
        {
            "code": "admin-basic",
            "name": "Admin Basic",
            "privilege_type": "admin",
            "price": "990.00",
            "duration_days": 30,
            "description": "Каркас под админ-привилегии и выдачу ролей через сайт.",
            "sort_order": 20,
            "metadata": {"placeholder": True},
        },
        {
            "code": "moderator-basic",
            "name": "Moderator Basic",
            "privilege_type": "moderation",
            "price": "590.00",
            "duration_days": 30,
            "description": "Каркас под модераторские роли и правила синхронизации.",
            "sort_order": 30,
            "metadata": {"placeholder": True},
        },
    ]:
        GameServerPrivilege.objects.get_or_create(server=server, code=defaults["code"], defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [("game_servers", "0001_initial")]

    operations = [migrations.RunPython(seed_public_server, migrations.RunPython.noop)]
