# CaseUP — Ideas / Product Backlog

Этот файл содержит идеи, планы, спорные решения и статус реализации по проекту CaseUP.

Codex и ChatGPT должны читать этот файл перед:
- реализацией новых функций;
- изменением архитектуры;
- работой с модулями;
- изменением магазина;
- изменением подписок;
- изменением UI/UX;
- изменением экономики проекта.

---

## Статусы

- `idea` — просто идея, ещё не проработана
- `planned` — принято в работу в будущем
- `in_progress` — реализация начата
- `partial` — реализовано частично
- `done` — реализовано
- `needs_rework` — реализовано, но требует переделки
- `rejected` — отклонено
- `paused` — отложено

---

## Приоритеты

- `P0` — критично
- `P1` — важно
- `P2` — желательно
- `P3` — когда-нибудь

---

## Идеи

---

### IDEA-001: Steam Catalog 2.0

**Статус:** partial  
**Приоритет:** P1  
**Категория:** modules / steam / store / UI  
**Версия идеи:** 0.3.2  
**Реализовано в версии:** не полностью  
**Связанные модули:** `steam_catalog_v2`, `steam_update_plugin_price_graphics`

#### Суть идеи

Steam Catalog 2.0 должен быть не просто темой, а полноценным runtime-модулем, который может заменять несколько связанных страниц каталога Steam.

#### Что должно работать

- модуль покупается через Store;
- после покупки доступен к применению;
- если применён — заменяет дефолтный каталог;
- может заменять не одну страницу, а группу страниц;
- должен поддерживать будущую темизацию;
- fallback на дефолтный partial обязателен.

#### Что уже реализовано

- [ ] покупка через Store
- [ ] entitlement
- [ ] применение модуля
- [ ] замена partial
- [ ] fallback
- [ ] тесты совместимости
- [ ] UI detail page
- [ ] preview в магазине

#### Что нужно доработать

- проверить совместимость с project version;
- проверить применение после покупки;
- проверить, не доступен ли модуль без нужной подписки;
- проверить fallback на дефолтные шаблоны.

#### Корректировки идеи

Нужно уточнить:
- должен ли пользователь применять один модуль каталога или несколько одновременно;
- может ли один runtime-модуль переопределять несколько зон интерфейса;
- как Store должен показывать dependency на Farm/Steam подписку.

---

### IDEA-002: Runtime module themes for all pages

**Статус:** idea  
**Приоритет:** P1  
**Категория:** architecture / modules / theming  
**Версия идеи:** 0.3.2  
**Реализовано в версии:** нет

#### Суть идеи

Сделать универсальный механизм, где каждая страница может смотреть, какой partial нужно загрузить:

1. если у пользователя куплен и применён runtime-модуль — грузить его partial;
2. иначе грузить дефолтный partial.

#### Пример

```text
dashboard page
├── default partial
└── runtime module partial, if applied

steam catalog page
├── default partial
└── steam_catalog_v2 partial, if applied

trade hub page
├── default partial
└── trade_hub_v2 partial, if applied
```

#### Что важно не сломать

- существующие runtime module manifests;
- точечные slot-override для Steam Catalog detail;
- fallback на дефолтный partial, если модуль не куплен, несовместим или не применён;
- совместимость с текущей версией проекта `0.3.2`.

#### Следующий шаг

Сначала расширять механизм только на страницы, где уже есть понятный slot/fallback. Не переписывать все шаблоны сразу.

---

## Bugfix / UX backlog

### BUG-001: Steam Catalog detail PNG item image rendering

**Статус:** done  
**Приоритет:** P1  
**Версия обнаружения:** 0.3.2  
**Исправлено в версии:** 0.3.2  
**Связанные зоны:** `frontend/templates/steam_catalog/partials/detail_content.html`, `frontend/static/css/modules/steam_catalog.css`

PNG предмета в detail-card не должен рендериться как аватар с `object-fit: cover`. Для предметов нужен отдельный media-блок с `object-fit: contain` и fallback без изображения.

### BUG-002: Dashboard sidebar needs internal scroll

**Статус:** done  
**Приоритет:** P1  
**Версия обнаружения:** 0.3.2  
**Исправлено в версии:** 0.3.2  
**Связанные зоны:** `frontend/static/css/layout.css`, `frontend/templates/components/sidebar_dashboard.html`

Левый dashboard sidebar должен иметь собственный scroll при маленькой высоте окна, чтобы нижние пункты меню оставались доступны независимо от scroll основного контента.

### BUG-003: Profile custom avatar upload UI

**Статус:** done  
**Приоритет:** P2  
**Версия обнаружения:** 0.3.2  
**Исправлено в версии:** 0.3.2  
**Связанные зоны:** `frontend/templates/profiles/partials/profile_content.html`, `frontend/templates/themes/midnight/pages/profile_content.html`, `frontend/static/css/modules/profiles.css`, `frontend/static/js/dashboard_shell.js`

Форма загрузки своей аватарки не должна выглядеть как сырой browser file input. Нужны стилизованный выбор файла, имя выбранного файла, preview при включённом JS и рабочий fallback без JS.

### BUG-004: Language switcher RU/EN works incorrectly inside dashboard shell

**Статус:** done  
**Приоритет:** P1  
**Версия обнаружения:** 0.3.2  
**Исправлено в версии:** 0.3.2  
**Связанные зоны:** `frontend/templates/components/language_switcher.html`, `frontend/templatetags/frontend_i18n.py`, `frontend/static/js/dashboard_shell.js`

Переключение RU/EN внутри dashboard shell должно выполнять full-page navigation, чтобы sidebar/topnav и active state обновлялись вместе с текущим языком.
