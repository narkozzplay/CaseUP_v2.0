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

Prod overlay публикует приложение на локальный порт `127.0.0.1:18001`. Для внешнего доступа сверху нужен reverse proxy Nginx или Caddy с проксированием на этот локальный порт и корректными заголовками `Host`/`X-Forwarded-Proto`.

## Типовые ошибки

- `web` в статусе `Restarting`, в логах `PermissionError: ... /app/staticfiles`: volumes созданы старой версией образа с владельцем root. Удалите их (база не пострадает) и пересоберите: `docker volume rm caseup_dev_static_data caseup_dev_media_data caseup_dev_backup_data caseup_dev_celerybeat_data`, затем `docker compose --env-file .env.dev up --build`.
- Docker daemon не запущен: запустите Docker Desktop или Docker Engine и повторите команду.
- Порт `8000`, `5432` или `6379` занят: поменяйте `DJANGO_PORT`, `POSTGRES_PORT` или `REDIS_PORT` в `.env.dev` либо остановите конфликтующий сервис.
- Отсутствует `.env.dev`: создайте его из `.env.dev.example` и запускайте compose с `--env-file .env.dev`.
- `caseup.localhost` не открывается: проверьте, что контейнер `web` запущен, порт `8000` опубликован, а браузер не подменяет `localhost` через proxy/VPN.
- `ALLOWED_HOSTS` не содержит нужный host: добавьте домен в `DJANGO_ALLOWED_HOSTS` и перезапустите `web`.
- Healthcheck показывает `unhealthy`: проверьте, что `DJANGO_HEALTHCHECK_HOST` входит в `DJANGO_ALLOWED_HOSTS` и соответствует host, который должен принимать Django.
