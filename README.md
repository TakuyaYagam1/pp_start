# Антиспам Telegram-бот

Telegram-бот для защиты групп и топиков от спама. Бот проверяет новых участников, удаляет неподтвержденных пользователей, анализирует сообщения по стоп-словам и при необходимости уточняет решение через LLM.

## Возможности

- Верификация новых участников через Telegram join request и приватную кнопку.
- До прохождения проверки пользователь не попадает в группу, не читает чат и не пишет сообщения.
- Таймаут подтверждения: 3 минуты.
- Корректная работа в Telegram-топиках через `message_thread_id`.
- Быстрая проверка сообщений по стоп-словам.
- Дополнительная LLM-проверка подозрительных сообщений.
- Flood-защита: одинаковый текст, стикер или медиа подряд удаляются с предупреждением, а повтор после предупреждения приводит к исключению.
- Два режима реакции на спам:
  - `delete`: удалить сообщение и заблокировать пользователя;
  - `notify_admin`: уведомить администратора.
- Логирование действий бота.
- Если пользователь не прошел проверку за 3 минуты, бот отклоняет заявку или кикает пользователя из группы.
- Redis для временного состояния верификации и служебных данных.

## Системный дизайн

```text
Telegram join request
        |
        | long polling
        v
Telegram bot container
  Python + aiogram
        |
        | private challenge, pending verification, TTL, flags
        v
Redis container

Spam flow:
message -> duplicate flood check -> stop words -> LLM check -> delete / notify admin -> log
```

Для v1 используется long polling: приложению не нужен публичный HTTP-порт, домен или TLS. Redis работает только во внутренней Docker Compose-сети и не публикуется наружу.

## Структура проекта

```text
pyproject.toml            # project metadata, runtime deps, Ruff/Pytest config
app/
  __main__.py              # package entrypoint: python -m app
  config/
    settings.py            # pydantic-settings и .env
  core/
    models.py              # domain models/enums
    stopwords.py           # domain stop-word rules
    services/              # application/domain services
    llm/
      client.py            # LLM client facade
      prompts.py           # LLM prompt builders
  cache/
    redis.py               # Redis client lifecycle and repositories
  tg_bot/
    handlers/              # Telegram transport routers: admin/user + feature routers
    keyboards/             # Telegram reply/inline UI builders
    middlewares/           # aiogram middleware extension points, Redis DI
    states/                # FSM extension points
    utils/                 # Telegram texts/helpers
```

Handlers remain thin: they read Telegram updates, validate transport-specific fields and call services. Business rules for verification, spam detection, moderation actions, Redis repositories and LLM access live outside Telegram handlers. This keeps tests focused and allows replacing the transport layer without rewriting core behavior.

PostgreSQL, Alembic, `app/database/` and `migrations/` are intentionally not included in v1: the current product requirements use Redis-only storage. A relational layer should be added only when persistent relational data is required.

## Хранилище и LLM

Бот не использует базу данных. Все служебное состояние хранится в Redis:

- `verify:{chat_id}:{user_id}` - pending verification с TTL, id приватного challenge-сообщения, `verification_chat_id`, `message_thread_id` для legacy-записей и временем создания;
- `verified:{chat_id}:{user_id}` - отметка, что пользователь прошел верификацию;
- `duplicate_message:{chat_id}:{user_id}` - текущая серия одинаковых сообщений пользователя с TTL;
- `duplicate_message_warning:{chat_id}:{user_id}` - digest flood-сообщения, за которое пользователь уже получил предупреждение;
- `llm:{sha256}` - кеш ответа LLM на нормализованный текст сообщения с TTL из `LLM_CACHE_TTL_SECONDS`.

При старте приложение выполняет `PING` Redis и восстанавливает таймеры для активных `verify:*` ключей. Если ключ поврежден или не имеет TTL, он безопасно удаляется и событие пишется в лог. Восстановленный timeout для join request отклоняет заявку и банит пользователя.

## UX верификации

Для полного сценария защиты группа должна использовать заявки на вступление: включите approve новых участников в настройках группы или создайте invite link с join request. В этом режиме пользователь сначала отправляет заявку, но еще не становится участником группы.

Бот получает `chat_join_request`, отправляет пользователю приватное сообщение с предупреждением `⚠️` и кнопкой `✅ Я человек`, затем ждет до `VERIFY_TIMEOUT_SECONDS`, по умолчанию 180 секунд. Пока пользователь не нажал кнопку, Telegram не дает ему читать чат и отправлять сообщения, потому что заявка еще не одобрена.

После нажатия кнопки бот вызывает `approve_chat_join_request`, удаляет приватное challenge-сообщение, отправляет личное `✅ Готово, доступ открыт`, отмечает пользователя как verified и очищает pending-запись. Если timeout истек, бот отправляет личное `❌ Проверка не пройдена`, вызывает `decline_chat_join_request`, `ban_chat_member` и удаляет pending-запись.

LLM-интеграция работает через OpenAI-compatible `/chat/completions`: задаются `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` и timeout. В обычном spam-flow LLM вызывается только после совпадения stop-word, а ответ кешируется на `LLM_CACHE_TTL_SECONDS`, по умолчанию 300 секунд. Для файлов без текста бот проверяет доступные метаданные: `file_name`, `mime_type`, emoji/set name стикера и caption. Отдельно бот детерминированно отслеживает одинаковые сообщения подряд: текст сравнивается по нормализованной строке, стикеры и медиа - по `file_unique_id`. При достижении `DUPLICATE_MESSAGE_WARN_THRESHOLD` бот удаляет накопленные дубли и предупреждает пользователя. Warning ставится атомарно и не дублируется при параллельной обработке сообщений; следующий duplicate-flood в течение `DUPLICATE_MESSAGE_WARNING_TTL_SECONDS` приводит к kick через ban/unban. Ответы LLM `да/yes` считаются спамом, `нет/no` - не спамом; при timeout, ошибке или непонятном ответе обычный stop-word flow применяет fallback на ключевые слова.

## Словари stop-words

Базовые spam-маркеры вынесены из Python-кода в packaged data:

- `app/core/data/stopwords/spam_ru.txt` - русские фразы;
- `app/core/data/stopwords/spam_en.txt` - английские фразы.

Формат простой: один термин или фраза на строку. Пустые строки и строки с `#` игнорируются, дубли убираются регистронезависимо. После изменения словарей достаточно пересобрать контейнер: `docker compose up -d --build`.

Внешние profanity-листы можно импортировать позже отдельной задачей, но их нельзя слепо смешивать с текущими spam-словами. Такие списки чаще ловят мат и оскорбления, а не рекламу казино, крипты, займов и мошеннических ссылок. Перед импортом нужно проверить лицензию, язык, качество терминов и риск ложных срабатываний.

## Стек

- Python: `python:3.14.5-slim-trixie`
- Telegram framework: `aiogram 3.x`
- Cache/state storage: `redis:8.8.0-alpine3.23`
- Runtime: Docker Compose
- CI: GitHub Actions, pytest, Ruff, Docker Buildx

## Быстрый старт

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

То же через Makefile:

```bash
make env-init
make install
make up
make logs-bot
```

Проверить состояние:

```bash
docker compose ps
docker compose logs -f bot
```

## Makefile

Основные команды для разработки и деплоя:

```bash
make help          # список команд
make install       # установка зависимостей .[dev]
make test          # pytest -q
make lint          # ruff check
make fmt           # ruff format
make check         # lint + format check + tests + compileall + compose config
make up            # docker compose up -d --build
make up-bot        # пересобрать и перезапустить только bot
make logs-bot      # логи bot
make redis-cli     # redis-cli внутри контейнера
make spam-log      # tail /app/logs/spam.log внутри bot
make clean         # удалить локальные cache-файлы
```

Остановить:

```bash
docker compose down
```

## Переменные окружения

Основные переменные задаются в `.env`:

```env
BOT_TOKEN=...
TELEGRAM_PROXY_URL=
REDIS_URL=redis://redis:6379/0
VERIFY_TIMEOUT_SECONDS=180
DUPLICATE_MESSAGE_WINDOW_SECONDS=60
DUPLICATE_MESSAGE_WARN_THRESHOLD=3
DUPLICATE_MESSAGE_WARNING_TTL_SECONDS=300
DUPLICATE_WARNING_MESSAGE_TTL_SECONDS=60
ACTION_MODE=notify_admin
ADMIN_USERNAME=@admin
ADMIN_ID=
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
LLM_TIMEOUT_SECONDS=8
LLM_CACHE_TTL_SECONDS=300
LOG_LEVEL=INFO
LOG_FILE=/app/logs/spam.log
```

Назначение переменных:

- `BOT_TOKEN` - токен Telegram-бота из BotFather.
- `REDIS_URL` - адрес Redis внутри Compose-сети.
- `VERIFY_TIMEOUT_SECONDS` - время ожидания верификации нового пользователя.
- `DUPLICATE_MESSAGE_WINDOW_SECONDS` - окно, в котором считаются одинаковые сообщения подряд от одного пользователя.
- `DUPLICATE_MESSAGE_WARN_THRESHOLD` - сколько одинаковых сообщений подряд нужно для удаления дублей и предупреждения.
- `DUPLICATE_MESSAGE_WARNING_TTL_SECONDS` - сколько действует предупреждение перед kick при новом таком же повторе.
- `DUPLICATE_WARNING_MESSAGE_TTL_SECONDS` - через сколько секунд удалить warning-сообщение бота из чата.
- `ACTION_MODE` - реакция на спам: `delete` или `notify_admin`.
- `ADMIN_USERNAME` / `ADMIN_ID` - fallback-получатель уведомлений для `notify_admin`.
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` - параметры OpenAI-compatible LLM provider.
- `LLM_CACHE_TTL_SECONDS` - сколько хранить ответ LLM для одного текста, по умолчанию 300 секунд.
- `LOG_LEVEL` и `LOG_FILE` - уровень логирования и путь к `spam.log`.

`ACTION_MODE` принимает значения:

- `delete`: удалить спам-сообщение и заблокировать пользователя;
- `notify_admin`: отправить уведомление администратору.

Режим можно переключать без изменения `.env` и без рестарта контейнера. Реальный администратор Telegram-чата может открыть панель командой `/admin` или `/help`, а затем выбрать режим inline-кнопками `Удалять спам`, `Только уведомлять` или `Сбросить к env`. Если команда вызвана в группе, бот удаляет команду из чата и отправляет админ-панель в ЛС администратору. Для этого администратор должен заранее открыть личный чат с ботом через `/start`. `ADMIN_ID` и `ADMIN_USERNAME` используются как fallback для личных команд и дефолтного получателя уведомлений.

Также доступны текстовые команды:

```text
/admin
/help
/mode
/mode delete
/mode notify_admin
/mode reset
/notify
/notify me
/notify @username
/notify 123456789
/notify reset
```

В меню Telegram `/` бот программно регистрирует только `/admin`, `/help`, `/mode` и `/notify` при старте через `bot.set_my_commands`. Для обычных участников групп и обычных личных чатов меню очищается, а команды показываются через `BotCommandScopeAllChatAdministrators` и персональный scope для `ADMIN_ID`. Аргументы вроде `/mode delete` и `/notify me` показываются внутри `/admin`, потому что Telegram command menu хранит только название команды и короткое описание.

Значение, заданное через `/mode delete` или `/mode notify_admin`, хранится в Redis per-chat в ключе `settings:action_mode:{chat_id}` и имеет приоритет над `.env` только для конкретного чата. Старый глобальный ключ `settings:action_mode` читается как fallback для совместимости с ранними версиями. Команда `/mode reset` удаляет runtime override для текущего чата и возвращает режим из `ACTION_MODE`. Получатель из `/notify ...` хранится в Redis per-chat в ключе `settings:notification_target:{chat_id}`; numeric id отправляет уведомления в ЛС, `@username` оставляет уведомление в чате с mention.

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

Логи:

```bash
docker compose logs -f bot
docker compose exec bot sh -lc 'tail -f /app/logs/spam.log'
```

`spam.log` содержит структурированные события верификации, timeout-удаления, спам-детекта, действий модерации и безопасно отредактированные ошибки Telegram API.

## CI/CD

В репозитории настроен CI для проверки инфраструктуры и контейнерной сборки.

CI выполняет:

- запуск на каждом `push` в любую ветку, на pull request и вручную через `workflow_dispatch`;
- отдельную job с pytest, если в репозитории есть тесты;
- выгрузку `pytest-results.xml` в артефакты workflow;
- проверку обязательных файлов;
- линтинг Python-кода через Ruff;
- проверку форматирования через Ruff;
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
- Бот должен быть администратором группы с правами на обработку заявок на вступление и ban пользователей: это требуется для приватной join-request верификации.
- Для `ACTION_MODE=delete` боту также нужны права на удаление сообщений и ban/unban пользователей.
- Контейнер бота запускается от non-root пользователя.
- Для контейнера включен `no-new-privileges`.
- Секреты хранятся только на сервере или в GitHub Secrets для CI/CD.
