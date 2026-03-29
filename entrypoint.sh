#!/bin/sh
set -e

if [ "${DB_ENGINE}" = "postgres" ]; then
  echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT} ..."
  while ! nc -z "${POSTGRES_HOST}" "${POSTGRES_PORT}"; do
    sleep 1
  done
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${RUN_COLLECTSTATIC:-0}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"