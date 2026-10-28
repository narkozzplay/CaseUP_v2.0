# Theme profiles and custom template integration

## Что уже реализовано

В `modules.v2.system` тема определяется записью `ThemeProfile` и тремя полями:

- `key` — стабильный идентификатор темы
- `template_base_path` — базовый путь до theme-specific шаблонов
- `css_file` — файл темы в `static`

Резолвинг идёт через `modules.v2.system.services.resolve_theme_template()`.
Если theme-specific шаблон не найден, система падает обратно в стандартный шаблон из `frontend/templates/components/*`.

Сейчас theme-aware включение заведено для:

- `components/head.html`
- `components/navbar_public.html`
- `components/footer.html`

Это значит, что новая тема не обязана переопределять всё сразу. Достаточно CSS и только тех компонентов, которые реально отличаются.

## Как добавить новую тему в проект

### 1. Создать static-файл темы

Пример:

- `frontend/static/css/themes/neon.css`

Внутри обычно переопределяются CSS variables из `frontend/static/css/base.css` и, при необходимости, конкретные UI-блоки.

### 2. Создать theme-specific шаблоны

Структура должна совпадать с `template_base_path`.
Для темы `neon` это: 

- `frontend/templates/themes/neon/components/head.html`
- `frontend/templates/themes/neon/components/navbar_public.html`
- `frontend/templates/themes/neon/components/footer.html`

Если какой-то файл отсутствует, будет использован стандартный fallback.

### 3. Создать `ThemeProfile`

Через admin или data migration:

- `key = "neon"`
- `template_base_path = "themes/neon"`
- `css_file = "css/themes/neon.css"`

Важно: `template_base_path` задаётся **без** `frontend/templates/`.

### 4. Активировать тему

Через `SiteConfiguration.active_theme`.
При этом:

- `is_default=True` — дефолтная тема платформы
- `is_test_only=True` — тема недоступна в prod
- `project_version_from / project_version_to` — ограничение по версиям проекта

## Как добавлять тему из plugin/app

Текущая архитектура уже позволяет подключать тему не только из `frontend`, но и из отдельного Django app/plugin, если он попадает в `INSTALLED_APPS`.

Это работает потому что в `config/settings/base.py` включено:

- `APP_DIRS = True`

### Для plugin-поставки темы

Плагин должен содержать: 

- `your_plugin/templates/themes/<theme_key>/components/*.html`
- `your_plugin/static/css/themes/<theme_key>.css`

После установки plugin app в `INSTALLED_APPS` Django начнёт видеть эти templates/static автоматически.

Далее нужен bootstrap плагина: 

1. plugin migration или plugin setup code создаёт `ThemeProfile`
2. `template_base_path` указывает на `themes/<theme_key>`
3. `css_file` указывает на `css/themes/<theme_key>.css`

## Рекомендуемый контракт для plugin themes

Чтобы потом не плодить несовместимые темы, придерживайся такого контракта:

- `theme key` уникален в рамках всей платформы
- plugin всегда поставляет хотя бы CSS + `head.html`
- plugin не должен ломать fallback: отсутствующие partials должны безопасно возвращаться к default
- если тема привязана к plugin feature, её лучше помечать `is_test_only=True`, пока плагин не стабилизирован

## Что важно для будущего plugin manager

Если позже будет отдельный модуль управления плагинами, для кастомных тем достаточно автоматизировать три шага:

1. зарегистрировать plugin app в `INSTALLED_APPS`
2. выполнить bootstrap/миграции plugin
3. создать или обновить `ThemeProfile` в БД

Отдельный theme engine переписывать не нужно. Текущий слой уже подходит как базовый runtime для plugin-provided themes.

## CaseUP Tactical (базовый шаблон)

Базовый дизайн платформы описан токенами в `frontend/static/css/base.css` (`--accent`, `--panel`, `--rarity-*`, `--font-*`).
Каркасы страниц (шапка, футер, главная, вход, 404, сайдбар и топбар дашборда) лежат в `frontend/static/css/shell.css`.
Тема `default` больше не дублирует компоненты: её `themes/default/components/*` подключают базовые `components/*`.

Опциональные картинки (логотип, favicon, фон главной, обложки модулей и т.д.) кладутся в `frontend/static/img/brand/`
и подхватываются тегом `{% brand_asset "имя" %}` из `caseup_brand`. Список слотов: `frontend/static/img/brand/README.md`.
Без картинок шаблон рисует градиентные заглушки.
