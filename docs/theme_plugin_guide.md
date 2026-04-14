# CaseUP Theme Plugin Guide

## 1) Что такое theme plugin в CaseUP

Theme plugin в CaseUP это безопасный runtime-пакет оформления, который:

- обнаруживается из файловой системы (`modules/plugins/themes/*`)
- не исполняет произвольный Python-код
- может влиять на UI через CSS и опциональные template overrides
- поддерживает scope (глобальный и модульный)

## 2) Где лежат темы

Папка runtime-источника:

- `modules/plugins/themes/<theme_key>/`

Минимум внутри должен быть файл:

- `theme.json`

Опционально тема может содержать статические файлы, например:

- `modules/plugins/themes/<theme_key>/css/<file>.css`

## 3) Как устроен manifest

Формат manifest:

```json
{
  "key": "theme_key",
  "name": "Theme name",
  "description": "Theme description",
  "author": "Author",
  "scope": ["global", "steam_catalog"],
  "css_files": ["theme_key/css/theme.css"],
  "template_overrides": {},
  "preview_image": "",
  "is_public": false,
  "compatible_project_versions": ["0.2.6"],
  "price_default": "1000.00"
}
```

## 4) Какие поля обязательны

Обязательные поля:

- `key`
- `name`
- `description`
- `author`
- `scope`
- `css_files`
- `is_public`

Если обязательные поля отсутствуют или невалидны, тема игнорируется.

## 5) Что такое scope темы

`scope` определяет, где применяется тема:

- `global` — глобально для интерфейса пользователя
- `dashboard` — dashboard-контур
- `steam_catalog` — только модуль Steam Catalog
- другие scope допустимы, если поддержаны проектом

## 6) Как runtime обнаруживает тему

Discovery выполняется сервисом `modules.v2.system.theme_plugins`:

- сканирует `modules/plugins/themes/*/theme.json`
- валидирует manifest
- игнорирует битые/неполные пакеты
- не падает при ошибках в отдельной теме

## 7) Как тема связывается с магазином

Store listing хранится отдельно от runtime-пакета:

- runtime source of truth: `modules/plugins/themes`
- store source: БД (`ThemeStoreListing`) с полем `theme_key`

Если listing указывает на отсутствующий `theme_key`, листинг отображается как runtime-недоступный.

## 8) Как пользователь получает доступ к теме

Доступ к runtime-теме есть, если:

- тема `is_public=true`, или
- тема куплена пользователем в Store (`UserThemePurchase`), или
- пользователь имеет `admin+` роль

Покупка даёт доступ, но не активирует тему автоматически.

## 9) Как выбрать тему локально

Локальный выбор хранится в `UserProfile.theme_preferences`:

- ключ: `scope`
- значение: `theme_key`

Выбор выполняется на странице профиля (блок Local themes).

## 10) Как создать новую кастомную тему

1. Создать папку `modules/plugins/themes/<theme_key>/`
2. Добавить `theme.json`
3. Добавить CSS-файлы, указанные в `css_files`
4. (Опционально) создать store listing с `theme_key`
5. Проверить рендер в нужном scope

## 11) Что разрешено и что не разрешено

Разрешено:

- CSS overrides
- template override декларации через manifest
- scope-aware оформление

Не разрешено в этой версии foundation:

- zip-upload тем пользователями
- dynamic execution пользовательского кода
- arbitrary Python plugin execution
- auto-install произвольных архивов

## 12) Как работает fallback, если тема сломана

Если пользовательская тема недоступна (невалидный manifest, несовместимая версия, потерянный пакет):

1. игнорируется локальный override
2. применяется глобальная тема сайта (`SiteConfiguration.active_theme`)
3. дальше стандартный fallback проекта

Проект не должен падать из-за одной сломанной темы.
