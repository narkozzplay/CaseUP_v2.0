# Сервер CaseUP: памятка

Короткая шпаргалка для ежедневной работы с сервером. Первичная установка — в [DEPLOY.md](DEPLOY.md).

| | |
|---|---|
| Сайт | https://case-up.ru, https://lib.case-up.ru |
| Сервер | `185.46.11.244` (Рег.облако, Ubuntu 24.04) |
| Папка проекта | `/opt/caseup` |
| Настройки | `/opt/caseup/.env.prod` |

> Пароли, ключи и содержимое `.env.prod` никому не присылайте и не показывайте на скриншотах.

---

## 1. Подключиться к серверу

На компьютере откройте **PowerShell** и выполните:

```powershell
ssh root@185.46.11.244
```

- Если спросит пароль — это пароль **root сервера** (из письма Рег.облака или тот, что вы задали командой `passwd`). При вводе символы не видны — это нормально. Вставка пароля — **правый клик** мышью.
- Подключились, когда видите строку вида `root@cv8143817:~#`.
- Выйти с сервера: `exit`.

### Вход без пароля (один раз)

В PowerShell **на компьютере** (не на сервере):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@185.46.11.244 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Пароль спросят последний раз, дальше `ssh root@185.46.11.244` будет входить сразу.

### Если ssh ругается `REMOTE HOST IDENTIFICATION HAS CHANGED`

Так бывает после переустановки сервера. В PowerShell на компьютере:

```powershell
ssh-keygen -R 185.46.11.244
```

и подключитесь заново (на вопрос ответьте `yes`).

---

## 2. Перейти в проект

Все команды ниже выполняются **на сервере** из папки проекта:

```bash
cd /opt/caseup
```

`dc` — это короткое имя для длинной команды
`docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml`.
Если сервер отвечает `dc: command not found`, верните его:

```bash
echo "alias dc='docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc
source ~/.bashrc
```

---

## 3. Обновить сайт

Когда в ветке `main` на GitHub появились изменения:

```bash
cd /opt/caseup
dc exec -T web python manage.py backup_db    # бэкап базы перед обновлением
git pull                                     # скачать новый код
dc up -d --build                             # пересобрать и перезапустить
docker image prune -f                        # удалить старые образы, чтобы не забивать диск
dc ps                                        # проверить, что всё запущено
```

- Сборка занимает 1–3 минуты, сайт недоступен примерно минуту.
- Миграции базы применяются автоматически при старте сайта.
- В `dc ps` у всех сервисов должно быть `Up` и `(healthy)` (у `caddy` — просто `Up`). Сразу после запуска может быть `(health: starting)` — подождите минуту и повторите `dc ps`.
- Откройте https://case-up.ru и убедитесь, что всё работает.

### Если `git pull` пишет про локальные изменения

Код на сервере руками не правится, поэтому такого быть не должно. Если всё же случилось — сбросить код к версии с GitHub (`.env.prod` и данные не затрагиваются):

```bash
git fetch origin && git reset --hard origin/main
```

### Откат на предыдущую версию

Если после обновления что-то сломалось:

```bash
git log --oneline -5                 # список последних версий, слева — их короткие номера
git checkout <номер-версии>          # например: git checkout eddec6a
dc up -d --build
```

Вернуться на актуальную версию: `git checkout main && git pull && dc up -d --build`.

> Если новая версия успела изменить базу (миграции), откат кода может не помочь — тогда восстановите бэкап, сделанный перед обновлением (раздел 7), или напишите разработчику.

---

## 4. Проверить состояние

```bash
dc ps                          # какие сервисы запущены и здоровы
dc logs --tail=100 web         # последние 100 строк логов сайта
dc logs -f web                 # логи в реальном времени (выход — Ctrl+C)
dc logs --tail=50 caddy        # HTTPS и входящие запросы
dc logs --tail=50 celery_worker
free -h                        # память
df -h /                        # место на диске
docker system df               # сколько места занимает Docker
```

Сервисы: `web` — сайт, `db` — база PostgreSQL, `redis` — очередь и кэш, `celery_worker` и `celery_beat` — фоновые задачи (сверка платежей, уведомления, очистка логов), `caddy` — HTTPS.

---

## 5. Перезапуск

```bash
dc restart web                 # перезапустить только сайт
dc up -d                       # поднять всё, что остановлено (безопасно запускать в любой момент)
reboot                         # перезагрузить сервер целиком; сайт поднимется сам через 1–2 минуты
```

---

## 6. Изменить настройки (`.env.prod`)

```bash
cd /opt/caseup
nano .env.prod                 # стрелки — перемещение, Ctrl+O, Enter — сохранить, Ctrl+X — выйти
dc up -d                       # пересоздать контейнеры с новыми настройками
```

Ключи Steam / FACEIT / Telegram удобнее менять в админке: **Control Center → Настройки → Интеграции** — там перезапуск не нужен.

---

## 7. Бэкапы

Создать бэкап вручную (база + загруженные файлы):

```bash
dc exec -T web python manage.py backup_db
```

Бэкапы лежат на сервере в `/var/lib/docker/volumes/caseup_prod_backup_data/_data`. Скачать их к себе — в PowerShell **на компьютере**:

```powershell
scp -r root@185.46.11.244:/var/lib/docker/volumes/caseup_prod_backup_data/_data $env:USERPROFILE\Documents\caseup-backups
```

Восстановление — только вместе с разработчиком: `dc exec web python manage.py restore_db --help`.

Ежедневный автоматический бэкап настраивается один раз через `crontab -e` — см. [DEPLOY.md, раздел 10](DEPLOY.md#10-резервные-копии).

---

## 8. Один раз после установки

Ограничить размер логов Docker, чтобы они не заняли весь диск:

```bash
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
systemctl restart docker
cd /opt/caseup && dc up -d --force-recreate
```

Обновления безопасности Ubuntu (раз в месяц):

```bash
apt update && apt -y upgrade
reboot                         # если после обновления написано "System restart required"
```

---

## 9. Если что-то не так

| Симптом | Что сделать |
|---|---|
| Сайт не открывается | `dc ps` — все ли сервисы `Up`. Если нет: `dc up -d`, затем `dc logs --tail=100 web`. |
| `502 Bad Gateway` | Сайт перезапускается или упал: подождите минуту, затем `dc logs --tail=100 web`. |
| Ошибка сертификата в браузере | `dc logs --tail=100 caddy`; проверьте, что DNS `case-up.ru` указывает на `185.46.11.244`. |
| `Bad Request (400)` | Домена нет в `DJANGO_ALLOWED_HOSTS` в `.env.prod`. |
| Не хватает места | `df -h /`, затем `docker image prune -f` и `docker builder prune -f`. |
| Сервис `unhealthy` | `dc logs --tail=100 <сервис>` — пришлите разработчику последние строки (без паролей). |
| Telegram «недоступен» | Telegram заблокирован в РФ — с российского сервера бот не работает, это ожидаемо. |
