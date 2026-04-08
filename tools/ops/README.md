# Production ops workflow

## Release

```powershell
./tools/ops/prod-release.ps1 -EnvFile .env.prod -ComposeProjectName caseup_prod -BaseUrl http://127.0.0.1:18001
```

## Manual sequence

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod build
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d db redis web celery_worker celery_beat
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py migrate --noinput
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py bootstrap_prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py smoke_check --base-url http://127.0.0.1:18001
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec celery_worker celery -A config inspect ping
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec celery_beat sh -lc "pgrep -f 'celery -A config beat'"
```

## Bootstrap examples

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py bootstrap_prod
docker compose exec web python manage.py grant_role "внутренний юзернейм" super_admin
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py bootstrap_prod --create-superuser-from-env
```

## Backup / restore

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py backup_db
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py backup_db --name before_release_20260317
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py restore_db --backup-dir before_release_20260317 --yes-i-understand-this-will-overwrite-data
```

Backups are written to `/app/backups` inside the web container and persisted in the `backup_data` Docker volume.
