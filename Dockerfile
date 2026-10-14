FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime tools only: nc for the DB wait in entrypoint.sh, pg_dump/pg_restore for backup_db/restore_db.
# All Python dependencies ship as binary wheels (psycopg[binary], Pillow, cryptography), so no compiler
# or libpq headers are needed in the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends netcat-openbsd postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY . /app

RUN chmod +x /app/entrypoint.sh

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser \
    && mkdir -p /app/staticfiles /app/media /app/backups /var/run/celery \
    && chown -R appuser:appgroup /app/staticfiles /app/media /app/backups /var/run/celery
USER appuser

EXPOSE 8000

ENTRYPOINT ["sh", "/app/entrypoint.sh"]
# Compose sets the command explicitly (runserver in dev, gunicorn in prod); a bare `docker run`
# gets the production server rather than Django's development one.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
