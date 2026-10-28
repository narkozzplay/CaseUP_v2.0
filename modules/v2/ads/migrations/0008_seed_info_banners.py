from django.db import migrations

# Starter info banners: text and an icon until the ChatGPT art (banners/info/prompts.md) is uploaded.
SEED = (
    ("dashboard", 10, "Рынок: продать скины", "market", "Рынок CaseUP", "Продай скины из инвентаря",
     "Выставь лот за минуту, деньги придут после сделки через гаранта сайта.", "Продать скин", "/market/sell/",
     "Sell skins from your inventory", "List a lot in a minute, you get paid after the site-guaranteed deal.", "Sell a skin"),
    ("dashboard", 20, "Бонус дня", "daily_bonus", "Бонусы", "Бонусы каждый день",
     "Забирай бонус дня, вводи промокоды и получай награду за друзей.", "Забрать бонус", "/bonus/",
     "Bonuses every day", "Claim the daily bonus, use promo codes and get rewards for friends.", "Claim bonus"),
    ("stats_top", 10, "Статистика CS2", "steam", "Steam + FACEIT", "Твоя статистика CS2",
     "K/D, HS %, винрейт и матчи из Steam и FACEIT на одной странице.", "", "",
     "Your CS2 stats", "K/D, HS %, win rate and matches from Steam and FACEIT on one page.", ""),
    ("stats_faceit", 10, "Подключи FACEIT", "trophy", "FACEIT", "Подключи FACEIT",
     "Уровень, ELO и последние матчи подтянутся сами и обновляются по кнопке.", "Подключить", "/profile/settings/",
     "Connect FACEIT", "Level, ELO and recent matches load on their own and refresh with one click.", "Connect"),
    ("donate_top", 10, "Поддержи CaseUP", "donations", "Помощь проекту", "Поддержи CaseUP",
     "Сайт живёт на донатах: каждый рубль идёт на серверы и новые функции.", "", "",
     "Support CaseUP", "The site runs on donations: every ruble goes to servers and new features.", ""),
    ("donate_bottom", 10, "Куда идут донаты", "system_settings", "Прозрачно", "Куда идут донаты",
     "Серверы, обновление цен каждые 5 минут и новые функции сайта.", "", "",
     "Where donations go", "Servers, price updates every 5 minutes and new site features.", ""),
    ("investments", 10, "Инвестиции в скины", "steam_investments", "Инвестиции", "Инвестиции в скины",
     "Записывай закупки кейсов и смотри прибыль по текущим ценам Steam.", "Добавить закупку", "/steam-trading/add/",
     "Skin investments", "Log your case purchases and see profit at current Steam prices.", "Add purchase"),
    ("cases", 10, "Кейсы", "case_simulator", "Симулятор", "Открой кейс CS2",
     "Выпадение как в игре. Предметы остаются на сайте и в Steam не выводятся.", "", "",
     "Open a CS2 case", "Drops like in the game. Items stay on the site and are never withdrawn to Steam.", ""),
    ("customization", 10, "Оформление профиля", "customization_catalog", "Только на CaseUP", "Выдели свой профиль",
     "Анимированные рамки, эффекты ника и фоны, которых нет в Steam.", "", "",
     "Make your profile stand out", "Animated frames, nickname effects and backgrounds Steam doesn't have.", ""),
)


def forwards(apps, schema_editor):
    InfoBanner = apps.get_model("ads", "InfoBanner")
    if InfoBanner.objects.exists():
        return
    InfoBanner.objects.bulk_create(
        InfoBanner(
            slot=slot, sort_order=order, name=name, icon=icon, eyebrow=eyebrow, title=title, text=text,
            button=button, url=url, title_en=title_en, text_en=text_en, button_en=button_en,
        )
        for slot, order, name, icon, eyebrow, title, text, button, url, title_en, text_en, button_en in SEED
    )


def backwards(apps, schema_editor):
    InfoBanner = apps.get_model("ads", "InfoBanner")
    InfoBanner.objects.filter(name__in=[row[2] for row in SEED], image="").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0007_info_banners"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
