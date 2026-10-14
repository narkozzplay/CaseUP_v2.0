# CaseUP Docker

## Dev запуск

Windows PowerShell:

```powershell
Copy-Item .env.dev.example .env.dev
docker compose --env-file .env.dev up --build
```

Linux/macOS:

```bash
cp .env.dev.example .env.dev
docker compose --env-file .env.dev up --build
```

Без флагов `-f` Compose сам добавляет `docker-compose.override.yml`: в нём dev-порты (`8000`, а также `5432` и `6379` на `127.0.0.1`) и монтирование исходников `.:/app` для автоперезагрузки. В `docker-compose.yml` порты и исходники не добавляйте: Compose складывает списки `ports`/`volumes` из всех файлов, и они попадут в прод.

С ngrok файлы перечисляются явно, поэтому override нужно указать самому:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.ngrok.yml up --build
```

Адреса:

- http://127.0.0.1:8000/
- http://caseup.localhost:8000/
- http://lib.caseup.localhost:8000/

## Проверка

```bash
docker compose --env-file .env.dev ps
docker compose --env-file .env.dev logs -f web
docker compose --env-file .env.dev exec web python manage.py check
docker compose --env-file .env.dev exec web python manage.py test
docker compose --env-file .env.dev exec web python manage.py createsuperuser
```

## Справочник CS2

Справочник скинов, кейсов и стикеров (страница `/steam-catalog/reference/`) загружается из открытого датасета ByMykel/CSGO-API. Команду можно запускать повторно, она обновит существующие записи.

```bash
# скины и кейсы (по умолчанию)
docker compose --env-file .env.dev exec web python manage.py import_cs2_reference
# вместе со стикерами
docker compose --env-file .env.dev exec web python manage.py import_cs2_reference --kinds skin,case,sticker
```

## Симуляция кейсов

Раздел `/cases/`: 4 кейса CS2, 10 кейсов CaseUP и терминал. Открытие списывает внутренний баланс (`LedgerReason.CASE_OPEN`), шансы по редкости как в CS2. Кейсы создаются миграцией `case_simulator.0002_seed_cases` из `modules/v2/case_simulator/data/cases.json`. Цены, состав и обложки (поле «image») правятся в Django admin.

```bash
# вернуть стандартный набор кейсов (перезапишет цены и содержимое)
docker compose --env-file .env.dev exec web python manage.py seed_case_simulator --update
```

## Пополнение баланса (ЮKassa)

Страница `/payments/topup/`, кнопка «+» у баланса в шапке. Платёж создаётся в ЮKassa, пользователь платит на её странице, баланс зачисляется через ledger (`LedgerReason.TOPUP`) после подтверждения: по webhook или при возврате на сайт. Статус всегда перепроверяется запросом к API ЮKassa, тело webhook на веру не берётся, повторные уведомления не зачисляют деньги дважды.

Без ключей и при `DEBUG=True` работает тестовая касса-заглушка (`PAYMENTS_PROVIDER=dev`), в проде без ключей пополнение выключено.

1. Зарегистрируйтесь на yookassa.ru и создайте тестовый магазин (в личном кабинете: «Тестовый магазин»).
2. «Интеграция → Ключи API»: `shopId` в `YOOKASSA_SHOP_ID`, секретный ключ (`test_...`) в `YOOKASSA_SECRET_KEY`.
3. «Интеграция → HTTP-уведомления»: URL `https://<домен>/payments/webhook/yookassa/`, события `payment.succeeded` и `payment.canceled`. Локально нужен публичный адрес, например ngrok (`docker-compose.ngrok.yml`). Без webhook баланс всё равно зачислится, когда пользователь вернётся на сайт.
4. Тестовая карта: `5555 5555 5555 4477`, любой срок и CVC.

Для боевого магазина замените ключи на боевые. Если в магазине включена онлайн-касса (54-ФЗ), поставьте `YOOKASSA_SEND_RECEIPT=1` (нужен email у пользователя) и при необходимости `YOOKASSA_VAT_CODE`.

Админ может менять баланс пользователя в настройках его профиля (карточка «Изменить баланс»): начислить, списать или задать точное значение. Каждая операция пишется в ledger с причиной и автором.

## Шифрование ключей в базе

API-ключи Steam и FACEIT, токены Telegram-бота и секрет webhook хранятся в базе в зашифрованном виде (`enc:v1:…`), а код и формы работают с открытым текстом. Ключ шифрования задаётся в `FIELD_ENCRYPTION_KEYS`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Без этой переменной ключ выводится из `DJANGO_SECRET_KEY`, и всё работает, но `check --deploy` в проде предупреждает (`caseup.W001`): если сменить `DJANGO_SECRET_KEY`, сохранённые ключи станут нечитаемыми.
- Чтобы перейти на отдельный ключ, добавьте `FIELD_ENCRYPTION_KEYS=<ключ>`, **не меняя** `DJANGO_SECRET_KEY`, перезапустите контейнеры и выполните `python manage.py reencrypt_secrets`.
- Ротация: поставьте новый ключ первым (`FIELD_ENCRYPTION_KEYS=<новый>,<старый>`), перезапустите и выполните `reencrypt_secrets`. После этого старый ключ можно убрать.
- Храните ключ вместе с бэкапами: из дампа базы без него сохранённые ключи не восстановить. Если расшифровать не удаётся, приложение пишет ошибку в лог и не стирает зашифрованное значение, поэтому, вернув правильный ключ, всё можно восстановить.

## Остановка

```bash
docker compose --env-file .env.dev down
```

## Полная очистка

```bash
docker compose --env-file .env.dev down -v
```

`down -v` удаляет Docker volumes, включая volume PostgreSQL с данными базы.

## Production-style запуск

```bash
cp .env.prod.example .env.prod
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Перед запуском замените значения:

- `SITE_BASE_URL`
- `CASEUP_LAB_BASE_URL`
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_HEALTHCHECK_HOST`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `POSTGRES_PASSWORD`

Prod overlay публикует приложение только на локальный порт `127.0.0.1:18001`; PostgreSQL и Redis наружу не публикуются. Для внешнего доступа сверху нужен reverse proxy Nginx или Caddy с проксированием на этот локальный порт и корректными заголовками `Host`/`X-Forwarded-Proto`.

В проде код берётся из образа, а не из папки на сервере, поэтому после `git pull` образ нужно пересобрать (`up --build` или `build`). Проверить итоговую конфигурацию можно так: `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml config`. В секциях `ports` должен быть только `127.0.0.1:18001`, а среди `volumes` не должно быть монтирования `/app` с хоста.

## Типовые ошибки

- `web` в статусе `Restarting`, в логах `PermissionError: ... /app/staticfiles`: volumes созданы старой версией образа с владельцем root. Удалите их (база не пострадает) и пересоберите: `docker volume rm caseup_dev_static_data caseup_dev_media_data caseup_dev_backup_data caseup_dev_celerybeat_data`, затем `docker compose --env-file .env.dev up --build`.
- Docker daemon не запущен: запустите Docker Desktop или Docker Engine и повторите команду.
- Порт `8000`, `5432` или `6379` занят: поменяйте `DJANGO_PORT`, `POSTGRES_PORT` или `REDIS_PORT` в `.env.dev` либо остановите конфликтующий сервис.
- Отсутствует `.env.dev`: создайте его из `.env.dev.example` и запускайте compose с `--env-file .env.dev`.
- `caseup.localhost` не открывается: проверьте, что контейнер `web` запущен, порт `8000` опубликован, а браузер не подменяет `localhost` через proxy/VPN.
- `ALLOWED_HOSTS` не содержит нужный host: добавьте домен в `DJANGO_ALLOWED_HOSTS` и перезапустите `web`.
- Healthcheck показывает `unhealthy`: проверьте, что `DJANGO_HEALTHCHECK_HOST` входит в `DJANGO_ALLOWED_HOSTS` и соответствует host, который должен принимать Django.
