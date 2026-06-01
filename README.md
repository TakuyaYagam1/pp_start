# Антиспам Telegram-бот

Telegram-бот для защиты групп и топиков от спама. Бот проверяет новых участников, удаляет неподтвержденных пользователей, анализирует сообщения по стоп-словам и при необходимости уточняет решение через LLM.

## Возможности

- Верификация новых участников через кнопку или команду `/verify`.
- Таймаут подтверждения: 3 минуты.
- Корректная работа в Telegram-топиках через `message_thread_id`.
- Быстрая проверка сообщений по стоп-словам.
- Дополнительная LLM-проверка подозрительных сообщений.
- Два режима реакции на спам:
  - `delete`: удалить сообщение и заблокировать пользователя;
  - `notify_admin`: уведомить администратора.
- Логирование действий бота.
- Redis для временного состояния верификации и служебных данных.

## Системный дизайн

```text
Telegram group / topics
        |
        | long polling
        v
Telegram bot container
  Python + aiogram
        |
        | pending verification, TTL, flags
        v
Redis container

Spam flow:
message -> stop words -> LLM check -> delete / notify admin -> log
```

Для v1 используется long polling: приложению не нужен публичный HTTP-порт, домен или TLS. Redis работает только во внутренней Docker Compose-сети и не публикуется наружу.

## Стек

- Python: `python:3.14.5-slim-trixie`
- Telegram framework: `aiogram 3.x`
- Cache/state storage: `redis:8.8.0-alpine3.23`
- Runtime: Docker Compose
- CI: GitHub Actions + Docker Buildx

## Быстрый старт

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

Проверить состояние:

```bash
docker compose ps
docker compose logs -f bot
```

Остановить:

```bash
docker compose down
```

## Переменные окружения

Основные переменные задаются в `.env`:

```env
BOT_TOKEN=...
BOT_RUN_MODE=polling
REDIS_URL=redis://redis:6379/0
VERIFY_TIMEOUT_SECONDS=180
ACTION_MODE=notify_admin
ADMIN_USERNAME=@admin
ADMIN_ID=
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
LLM_TIMEOUT_SECONDS=8
LOG_LEVEL=INFO
LOG_FILE=/app/logs/spam.log
```

`ACTION_MODE` принимает значения:

- `delete`: удалить спам-сообщение и заблокировать пользователя;
- `notify_admin`: отправить уведомление администратору.

## Запуск на сервере

На сервере должны быть установлены Docker и Docker Compose.

```bash
git clone <repo-url> anti-spam-telegram-bot
cd anti-spam-telegram-bot
cp .env.example .env
nano .env
docker compose up -d --build
```

Обновление:

```bash
git pull
docker compose up -d --build
docker compose ps
```

Адрес сервера, токены и реальные ключи не хранятся в репозитории.

## CI/CD

В репозитории настроен CI для проверки инфраструктуры и контейнерной сборки.

CI выполняет:

- проверку обязательных файлов;
- валидацию `docker compose config`;
- сборку Docker-образа через Buildx;
- запуск Redis;
- проверку Redis через `redis-cli ping`.

Сборка использует GitHub Actions cache:

```yaml
cache-from: type=gha
cache-to: type=gha,mode=max
```

Деплой выполняется вручную через `git pull` и `docker compose up -d --build`.

## Безопасность

- `.env` не коммитится.
- Redis не публикуется наружу.
- Контейнер бота запускается от non-root пользователя.
- Для контейнера включен `no-new-privileges`.
- Секреты хранятся только на сервере или в GitHub Secrets для CI/CD.
