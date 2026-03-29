param(
    [string]$EnvFile = ".env.prod",
    [string]$ComposeProjectName = "caseup_prod",
    [string]$BaseUrl = "http://127.0.0.1:18001"
)

$ErrorActionPreference = "Stop"
$compose = "docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file $EnvFile"

$env:COMPOSE_PROJECT_NAME = $ComposeProjectName

Invoke-Expression "$compose build"
Invoke-Expression "$compose up -d db redis web celery_worker celery_beat"
Invoke-Expression "$compose exec web python manage.py migrate --noinput"
Invoke-Expression "$compose exec web python manage.py collectstatic --noinput"
Invoke-Expression "$compose exec web python manage.py bootstrap_prod"
Invoke-Expression "$compose exec web python manage.py check"
Invoke-Expression "$compose exec web python manage.py smoke_check --base-url $BaseUrl"
Invoke-Expression "$compose exec celery_worker celery -A config inspect ping"
Invoke-Expression "$compose exec celery_beat sh -lc \"pgrep -f 'celery -A config beat'\""
