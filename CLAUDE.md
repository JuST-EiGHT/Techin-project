# CLAUDE.md — контекст проекта для Claude Code

Этот файл читается автоматически в начале каждой сессии. Держи его актуальным:
после каждого завершённого шага обнови раздел «Текущий статус».

## Что это за проект

SaaS-сервис аналитики и автоматизации для продавцов Ozon — аналог MPStats,
по модели подписки. Владелец — продавец оригинальных подшипников (NACHI) на Ozon FBO,
поставщик — компания ТЕХИНТЕЛС. Сервис сначала делается для собственного магазина,
потом открывается другим продавцам.

Что сервис должен уметь в итоге: P&L за любой период, прибыль по каждому SKU,
учёт себестоимости, анализ акций, расчёт минимальной цены, безопасное изменение
цен через Ozon API, остатки по складам, планирование поставок, аномалии с
уведомлениями в Telegram, тарифы и оплата.

Полное описание проекта — зачем он, что получится, архитектура, решения, риски —
в `docs/PROJECT.md`. Прочитай его в первой сессии.

## Стек

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2
- БД: PostgreSQL 16 (asyncpg). В тестах — SQLite в памяти (aiosqlite)
- Очереди: Celery + Redis
- Frontend: Next.js 14 (App Router), TypeScript, Tailwind CSS, TanStack Query, Recharts
- Всё запускается через Docker Compose

## Структура

```
backend/app/
  api/v1/        роутеры (router.py собирает их под /api/v1)
  core/          config.py (настройки из .env), errors.py (единый формат ошибок)
  crud/          функции работы с БД
  db/            base.py (DeclarativeBase), session.py (get_db)
  models/        SQLAlchemy-модели; каждую новую импортировать в models/__init__.py
  schemas/       Pydantic-схемы
  services/      бизнес-логика (security.py, auth.py, token_blacklist.py …)
  integrations/  Ozon API клиент, XLSX-парсеры (появятся в фазах 1–2)
  engine/        чистые расчётные модули без БД/HTTP (P&L, цены, поставки) — появится в фазе 3
  tasks/         Celery
backend/alembic/versions/   миграции, нумеруются 0001_, 0002_ …
backend/tests/              pytest; fixtures/ — образцовые XLSX-отчёты Ozon
frontend/src/               app/, components/, lib/, contexts/, hooks/
docs/                       бизнес-логика, Ozon API, roadmap, промпты по шагам
reference/                  рабочий HTML-калькулятор P&L (эталон логики парсинга) и CSV себестоимостей
```

## Команды

```bash
cp .env.example .env                       # первый запуск
# затем в .env задать ENCRYPTION_KEY (команда генерации — в .env.example), иначе API не стартует
docker compose up --build                  # всё приложение
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "..."

# Тесты бэкенда без Docker:
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pytest
```

## Правила разработки (обязательные)

1. **Один шаг roadmap за раз.** Не делай работу следующих шагов «заодно».
   Шаги описаны в `docs/ROADMAP.md`, промпты — в `docs/prompts/`.
2. **Тесты к каждому шагу.** Шаг не считается готовым, пока `pytest` не зелёный.
   Расчётные модули сверяй с эталонными цифрами из `docs/BUSINESS_LOGIC.md`.
3. **Миграции только через Alembic.** Никаких `create_all` в рабочем коде (только в тестах).
4. **Excel-отчёты Ozon парсить семантически** — искать таблицу и колонки по заголовкам
   с нормализацией текста. Никогда не завязываться на номер листа, строки или колонки.
5. **Себестоимость неизвестного товара — не 0.** Такой товар попадает в список
   «не найдена себестоимость» и показывается пользователю.
6. **Изменение данных в Ozon (цены, акции) — только через dry-run → подтверждение →
   аудит-лог.** Подробности в `docs/BUSINESS_LOGIC.md`, раздел «Безопасность».
7. **API-ключи Ozon** хранить только зашифрованными (Fernet), никогда не логировать
   и не возвращать в ответах API.
8. **Ошибки — понятные пользователю, на русском**, в формате `{"detail": "...", "code": N}`
   (см. `core/errors.py`).
9. **Интерфейс — на русском**, тёмная тема (фон #0f1117, карточки #1a1d27,
   рамки #2a2d3a, акцент #4f8ef7, зелёный #22c55e, красный #f87171, жёлтый #fbbf24).
10. **Ozon API меняется.** Перед реализацией интеграции сверяй эндпоинты с
    https://docs.ozon.ru/api/seller/ — список в `docs/OZON_API.md` мог устареть.
11. Не коммить `.env`, ключи и реальные выгрузки клиентов (кроме fixtures в tests/).

## Текущий статус

| Шаг | Что | Статус |
|---|---|---|
| 0.1 | Каркас проекта, Docker Compose | ✅ готово |
| 0.2 | Авторизация на бэкенде (JWT, register/login/refresh/logout/me) | ✅ готово, 21 тест |
| 0.3 | Авторизация на фронтенде (httpOnly cookies, /login, /register, /dashboard) | ✅ готово |
| 0.4 | Хранение API-ключей Ozon (шифрование, /dashboard/settings/api-keys) | ✅ готово, 42 теста всего |
| 1.1 | Универсальный клиент Ozon (retry, rate limit, фабрика) | ✅ готово, 68 тестов всего |
| 1.2 … 9.2 | см. `docs/ROADMAP.md` | ⏳ |

Детали сделанного в 0.2: access-токен 24 ч, refresh 30 дней; refresh одноразовый
(ротация); logout отзывает токены через чёрный список в Redis (в тестах — в памяти);
ошибки валидации отдаются как 400 с русским текстом.

Детали сделанного в 0.3: токены живут только в httpOnly cookies (`access_token`,
`refresh_token`), JS браузера их не видит. Браузер ходит к бэкенду через route
handlers Next: `/api/session/{login,refresh,logout}` ставят/чистят cookies,
`/api/session/proxy/<путь>` → `<API>/api/v1/<путь>` с `Authorization` из cookie
(методы выдачи токенов через прокси закрыты). Адрес бэкенда для сервера Next —
`API_URL` (в Docker `http://api:8000`), иначе `NEXT_PUBLIC_API_URL`.
`src/lib/api.ts` — axios: при 401 один общий refresh и повтор, иначе `/login?next=…`.
`src/middleware.ts` пускает на `/dashboard/*` только при наличии cookie сессии.
В nginx на бэкенд проксируется только `/api/v1/`, остальное `/api/*` — Next.

Детали сделанного в 0.4: таблица `api_credentials` (миграция 0002), Api-Key — только
Fernet-шифротекст (`services/encryption.py`; ключ проверяется в lifespan при старте).
`services/ozon_auth.py` проверяет ключ запросом `POST /v3/product/list` (limit=1):
401/400/404 → неверные ключи, 403 → нет прав (или «ключ отключён/истёк» по тексту Ozon),
429/5xx/сеть → Ozon недоступен. `/api/v1/credentials`: POST сохраняет только принятый
Ozon ключ (иначе 400, при недоступности Ozon — 503), GET — без ключа, Client-Id как
`••••1234`, DELETE и POST `/{id}/verify` (invalid/forbidden → `is_active=false`,
недоступность Ozon состояние не меняет). Чужой ключ — 404. Срок по умолчанию — +6
календарных месяцев; `days_until_expiry` считает бэкенд, фронт предупреждает при < 30.
Тест `test_api_key_never_returned_or_logged` ловит ключ в ответах и логах (DEBUG).
Эндпоинт Ozon с официальной документацией не сверен — docs.ozon.ru недоступен из
облачной среды разработки (на шаге 1.1 тоже); сверить на реальном ключе.

Детали сделанного в 1.1 (`app/integrations/ozon/`): все запросы к Ozon — только через
`OzonClient.request(method, path, json, items=N)`; `items` — число товаров в запросе
(для лимита 30 000 операций/мин), клиент резервирует их перед каждой попыткой.
Повторы: сеть/таймаут — 3 повтора (1, 2, 4 с); 429 — до 6 повторов (Retry-After ≤ 60 с
или 1…30 с); 5xx — 2 повтора через 5 с; 401/403/прочие 4xx — сразу исключение.
Иерархия: `OzonAPIError` → `OzonAuthError`, `OzonRateLimitError`, `OzonServerError`,
`OzonTimeoutError`, `OzonNetworkError`, `OzonNotConfiguredError`; у ошибки есть
`status_code`, `ozon_message` (ключ вырезан), `path`. Rate limiter — скользящее окно
60 с в Redis (sorted set + Lua, атомарно для всех воркеров), без Redis — в памяти;
партия резервируется целиком или ждёт. `get_ozon_client(user_id, db)` берёт активный
неистёкший ключ (последний использованный), обновляет `last_used_at`, клиент закрывать
(`async with`). Проверка ключа из 0.4 (`services/ozon_auth.py`) работает через тот же
клиент без повторов. Тесты лимитера против реального Redis: `TEST_REDIS_URL=redis://… pytest`.

## Где что искать

- Описание проекта целиком → `docs/PROJECT.md`
- Формулы P&L, себестоимость, минимальная цена, поставки, аномалии, эталонные цифры
  для тестов → `docs/BUSINESS_LOGIC.md`
- Эндпоинты Ozon Seller API, лимиты, устаревшие методы → `docs/OZON_API.md`
- План по фазам и шагам → `docs/ROADMAP.md`
- Готовые промпты на каждый шаг → `docs/prompts/`
- Рабочий JS-калькулятор P&L (логика парсинга двух типов отчётов) →
  `reference/ozon_calculator.html`
- Себестоимости 78 артикулов → `reference/cost_prices.csv`
- Образцовые отчёты Ozon → `backend/tests/fixtures/`
