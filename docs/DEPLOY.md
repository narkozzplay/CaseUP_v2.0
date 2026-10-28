# Запуск CaseUP на VPS

Первичная установка сервера. Ежедневная работа (подключение, обновление, логи, бэкапы) — в памятке [SERVER.md](SERVER.md).

Пошаговая инструкция: чистый сервер Ubuntu 24.04 → рабочий сайт на `https://case-up.ru` с HTTPS.
Все команды из блоков `bash` выполняются на сервере, если не сказано иное.

Что получится:

```
интернет ──443/80──▶ caddy (HTTPS, сертификаты Let's Encrypt, /media/)
                        │
                        ▼
                      web (Django + gunicorn) ──▶ db (PostgreSQL)   ← наружу не открыты
                      celery_worker, celery_beat ──▶ redis
```

## 0. Что нужно

- VPS: Ubuntu 24.04 LTS, 2 vCPU, 4 ГБ RAM, 40+ ГБ диска, публичный IPv4.
- Домен (здесь `case-up.ru`).
- Steam Web API key: <https://steamcommunity.com/dev/apikey>, в поле домена укажите `case-up.ru`.

## 1. DNS: направить домен на сервер

В кабинете reg.ru: **Домены → case-up.ru → DNS-серверы и управление зоной**.

1. Где править зону:
   - почта на домене (`support@case-up.ru`) живёт на виртуальном хостинге reg.ru → DNS-серверы оставьте хостинговые (`ns1.hosting.reg.ru`…), а записи правьте в ispmanager хостинга: **Управление DNS → case-up.ru**. Тогда MX-записи почты сохранятся;
   - почта не нужна → переключите на бесплатные **DNS-серверы REG.RU** (`ns1.reg.ru`, `ns2.reg.ru`) и правьте зону в кабинете reg.ru.
2. Старые A-записи для `@`, `www` (и `lib`, если есть) указывают на виртуальный хостинг: замените их значение на IP сервера, должно получиться:

| Тип | Имя (субдомен) | Значение   |
|-----|----------------|------------|
| A   | `@`            | IP сервера |
| A   | `www`          | IP сервера |
| A   | `lib`          | IP сервера |

MX-, TXT-записи (почта, SPF/DKIM) и A-записи `mail`, `smtp`, `pop`, `imap`, `ftp` не трогайте: они должны и дальше вести на хостинг. Изменения расходятся от 15 минут до нескольких часов. Проверка с компьютера (PowerShell):

```powershell
nslookup case-up.ru
nslookup lib.case-up.ru
```

Должен вернуться IP сервера. **Не запускайте сайт, пока DNS не обновился**: Caddy не сможет получить сертификат.

## 2. Подключение к серверу

С компьютера (PowerShell):

```powershell
ssh root@<IP сервера>
```

При первом подключении ответьте `yes`. Если вход по паролю — пароль из письма/кабинета reg.ru (при вводе символы не отображаются).

## 3. Подготовка сервера (один раз)

```bash
# обновления и часовой пояс
apt update && apt -y upgrade
timedatectl set-timezone Europe/Moscow

# файл подкачки 2 ГБ: страховка от нехватки памяти при сборке и пиках
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# файрвол: только SSH и веб
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443 && ufw --force enable

# Docker + Compose
curl -fsSL https://get.docker.com | sh
docker compose version
```

## 4. Код проекта

```bash
git clone https://github.com/narkozzplay/CaseUP_v2.0.git /opt/caseup
cd /opt/caseup

# короткая команда вместо длинного docker compose ... (работает из /opt/caseup)
echo "alias dc='docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc
source ~/.bashrc
```

## 5. Настройки `.env.prod`

```bash
cp .env.prod.example .env.prod

# сгенерировать секреты (скопируйте вывод в .env.prod)
echo "DJANGO_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "FIELD_ENCRYPTION_KEYS=$(openssl rand -base64 32 | tr '+/' '-_')"

nano .env.prod
```

В редакторе `nano`: стрелками к строке, правка, **Ctrl+O → Enter** — сохранить, **Ctrl+X** — выйти.

Что заменить:

| Переменная | Значение |
|---|---|
| `CADDY_DOMAIN` | `case-up.ru` |
| `SITE_BASE_URL` | `https://case-up.ru` |
| `DJANGO_ALLOWED_HOSTS` | `case-up.ru,www.case-up.ru,lib.case-up.ru` |
| `DJANGO_HEALTHCHECK_HOST` | `case-up.ru` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://case-up.ru,https://www.case-up.ru,https://lib.case-up.ru` |
| `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `FIELD_ENCRYPTION_KEYS` | сгенерированные выше |
| `DJANGO_SECURE_HSTS_SECONDS` | на первые дни `3600`; когда HTTPS стабильно работает — `31536000` |
| `SOCIAL_AUTH_STEAM_API_KEY` | ключ Steam Web API |
| `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` | пока тестовые (`test_...`) или пусто — пополнение будет выключено |

**Сохраните копию `.env.prod` у себя** (в менеджере паролей): без `FIELD_ENCRYPTION_KEYS` и паролей бэкапы не восстановить.

## 6. Запуск

```bash
cd /opt/caseup
dc up -d --build
dc ps
```

Первая сборка занимает несколько минут. В `dc ps` все сервисы должны быть `running`, у `web`, `db`, `redis`, `celery_*` — `healthy` (через 1–2 минуты).

Проверить выдачу сертификата и логи:

```bash
dc logs -f caddy     # ищите "certificate obtained successfully"; выход — Ctrl+C
dc logs -f web
```

Откройте `https://case-up.ru` и `https://lib.case-up.ru`.

## 7. Первый вход и наполнение

```bash
# администратор (получит роль super admin)
dc exec web python manage.py createsuperuser

# справочник скинов, кейсов и стикеров CS2
dc exec web python manage.py import_cs2_reference --kinds skin,case,sticker
```

Дальше в браузере, под администратором:

1. **Control Center → Настройки → «Основное»**: владелец сайта (например «Иванов Иван Иванович (самозанятый)»), реквизиты (ИНН, регион), e-mail поддержки. Они подставляются в соглашение и политику. Публичный адрес можно оставить пустым — возьмётся `SITE_BASE_URL`.
2. **Control Center → Система**: проверить, что интеграции и Celery зелёные.
3. Вход через Steam с обычного аккаунта, открытие кейса, вкладка **Логи** — события должны появляться.

## 8. ЮKassa

1. В кабинете ЮKassa → «Интеграция → HTTP-уведомления»: `https://case-up.ru/payments/webhook/yookassa/`, события `payment.succeeded` и `payment.canceled`.
2. Ключи в `.env.prod`, затем пересоздать контейнеры и проверить:

```bash
dc up -d
dc exec web python manage.py check_yookassa
```

Пока ключи тестовые, на сайте платят тестовой картой `5555 5555 5555 4477`. Переходите на боевые ключи только после проверки всего сценария.

## 9. Обновление сайта

Когда в `main` появились изменения:

```bash
cd /opt/caseup
git pull
dc up -d --build
```

Миграции применяются автоматически при старте `web`. Сайт недоступен несколько секунд, пока перезапускается контейнер.

## 10. Резервные копии

Сайт делает бэкап сам каждую ночь в 04:15 (фоновая задача), отправку копий на хостинг Рег.ру настройте по [SERVER.md, раздел 7а](SERVER.md#7а-бэкапы-на-хостинг-регру). Строки для `crontab` ниже больше не нужны — оставлены для истории, не добавляйте их.

Старый способ — ежедневный бэкап через cron в 04:15, хранение 14 дней:

```bash
crontab -e
```

Добавьте в конец две строки (редактор при первом запуске спросит, какой использовать — выберите `nano`):

```
15 4 * * * cd /opt/caseup && docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml exec -T web python manage.py backup_db >> /var/log/caseup-backup.log 2>&1
45 4 * * * find /var/lib/docker/volumes/caseup_prod_backup_data/_data -mindepth 1 -maxdepth 1 -mtime +14 -exec rm -rf {} +
```

Бэкапы лежат на том же сервере, поэтому время от времени скачивайте их к себе (PowerShell на компьютере):

```powershell
scp -r root@<IP сервера>:/var/lib/docker/volumes/caseup_prod_backup_data/_data ./caseup-backups
```

Восстановление: `dc exec web python manage.py restore_db --help`.

## Полезные команды

```bash
dc ps                          # состояние сервисов
dc logs -f --tail=200 web      # логи сайта
dc restart web                 # перезапуск сайта
dc exec web python manage.py shell
dc down                        # остановить всё (данные в volumes сохраняются)
df -h && free -h               # место на диске и память
```

## Если что-то не так

| Симптом | Причина и что делать |
|---|---|
| Браузер: «соединение не защищено», в `dc logs caddy` ошибки ACME | DNS ещё не указывает на сервер или закрыт порт 80. Проверьте `nslookup`, `ufw status`, затем `dc restart caddy`. |
| `Bad Request (400)` | Домена нет в `DJANGO_ALLOWED_HOSTS`. Исправьте `.env.prod`, `dc up -d`. |
| `403 CSRF verification failed` | Адреса нет в `DJANGO_CSRF_TRUSTED_ORIGINS` (с `https://`). |
| `web` в статусе `unhealthy` | `DJANGO_HEALTHCHECK_HOST` не входит в `DJANGO_ALLOWED_HOSTS`; смотрите `dc logs web`. |
| Вход через Steam падает | Пустой или неверный `SOCIAL_AUTH_STEAM_API_KEY`. |
| Не хватает памяти, контейнеры перезапускаются | Проверьте `free -h`; уменьшите `WEB_CONCURRENCY` до `1` или возьмите тариф с большей памятью. |
| Не грузятся аватары и фоны | `dc logs caddy`; файлы лежат в volume `caseup_prod_media_data`. |
