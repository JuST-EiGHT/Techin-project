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
уведомлениями в Telegram, деньги на счетах (банки по API, ДДС, платёжный календарь),
главный дашборд, тарифы и оплата.

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
7. **API-ключи Ozon и токены банков** хранить только зашифрованными (Fernet), никогда не
   логировать и не возвращать в ответах API.
8. **Ошибки — понятные пользователю, на русском**, в формате `{"detail": "...", "code": N}`
   (см. `core/errors.py`).
9. **Интерфейс — на русском**, тёмная тема по умолчанию (фон #0f1117, карточки #1a1d27,
   рамки #2a2d3a, акцент #4f8ef7, зелёный #22c55e, красный #f87171, жёлтый #fbbf24) и
   светлая (фон #f6f7fb, карточки #ffffff, рамки #e3e6ee, акцент #2563eb, зелёный #15803d,
   красный #dc2626, жёлтый #b45309) — переключается в «Основных настройках». Цвета — только
   токенами темы (bg, card, line, fg, muted, faint, accent, ok, bad, warn), без жёстких hex и
   `slate-*` (белый текст на цветной кнопке допустим); новая страница добавляется в общее меню
   (`src/lib/nav.ts`), её настройки — в меню аккаунта.
10. **Ozon API меняется.** Перед реализацией интеграции сверяй эндпоинты с официальной
    документацией: её выгрузка — `docs/ozon/seller_api_2026-10.txt` (docs.ozon.ru из облачной
    среды недоступен). `docs/OZON_API.md` — наша выжимка, при расхождении обнови её.
11. Не коммить `.env`, ключи и реальные выгрузки клиентов (кроме fixtures в tests/).
12. **История данных — главная ценность.** Исходные данные (снимки цен и остатков, начисления,
    участие в акциях, файлы отчётов, себестоимость) не удалять и не перезаписывать: изменение —
    новая запись. Итоги — производные, их можно пересчитать. Нет данных ≠ 0.
    Подробно — `docs/DATA_HISTORY.md`.
13. **Сервис универсальный — для любого продавца.** Не зашивать в код данные владельца
    (поставщик, банк, ставки, график выплат, условия оплаты): это настройки пользователя
    с разумными шаблонами по умолчанию.

## Текущий статус

| Шаг | Что | Статус |
|---|---|---|
| 0.1 | Каркас проекта, Docker Compose | ✅ готово |
| 0.2 | Авторизация на бэкенде (JWT, register/login/refresh/logout/me) | ✅ готово, 21 тест |
| 0.3 | Авторизация на фронтенде (httpOnly cookies, /login, /register, /dashboard) | ✅ готово |
| 0.4 | Хранение API-ключей Ozon (шифрование, /dashboard/settings/api-keys) | ✅ готово, 42 теста всего |
| 0.5 | Каркас интерфейса: меню с выпадающими списками, меню аккаунта, настройки, светлая тема | ✅ готово, 259 тестов всего |
| 1.1 | Универсальный клиент Ozon (retry, rate limit, фабрика) | ✅ готово, 68 тестов всего |
| 1.2 | Синхронизация товаров и цен (Celery, Beat, /sync, статус в шапке) | ✅ готово, 91 тест всего |
| 1.3 | Склады и остатки (FBO по складам и кластерам, FBS/rFBS, /dashboard/warehouses) | ✅ готово, 139 тестов всего |
| 1.4 | Финансовые начисления из API (by-day + postings, Celery, /finance) | ✅ готово, 171 тест всего |
| 1.5 | Акции Ozon (v2-методы, автодобавление, маржа, /dashboard/promotions) | ✅ готово, 197 тестов всего |
| 2.1 | Парсер XLSX-отчётов Ozon (детектор, начисления, реализация, POST /reports/parse) | ✅ готово, 242 теста всего |
| 2.2 | Загрузка отчётов и история (/dashboard/reports, конфликты периода, скрытие, перечитывание) | ✅ готово, 254 теста всего |
| 2.3 … 9.2 | см. `docs/ROADMAP.md` | ⏳ |

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
недоступность Ozon состояние не меняет). Чужой ключ — 404. Срок ключа берётся из Ozon
(`POST /v1/roles` → `expires_at`) при добавлении и каждой проверке; если Ozon его не
сообщил — введённый пользователем или +3 месяца (срок ключа по документации Ozon);
`days_until_expiry` считает бэкенд, фронт предупреждает при < 30.
Тест `test_api_key_never_returned_or_logged` ловит ключ в ответах и логах (DEBUG).
Сверено с официальной документацией (`docs/ozon/`): проверка — `/v1/roles`, затем
`/v3/product/list` (limit=1); тексты ошибок ключа распознаются по «Частым ошибкам» Ozon.

Детали сделанного в 1.1 (`app/integrations/ozon/`): все запросы к Ozon — только через
`OzonClient.request(method, path, json)`; перед каждой попыткой клиент берёт слот в
rate limiter: 50 запросов/с на Client ID (документация Ozon) плюс `METHOD_LIMITS` для
методов со своим лимитом.
Повторы: сеть/таймаут — 3 повтора (1, 2, 4 с); 429 — до 6 повторов (Retry-After ≤ 60 с
или 1…30 с); 5xx — 2 повтора через 5 с; 401/403/прочие 4xx — сразу исключение.
Иерархия: `OzonAPIError` → `OzonAuthError`, `OzonRateLimitError`, `OzonServerError`,
`OzonTimeoutError`, `OzonNetworkError`, `OzonNotConfiguredError`; у ошибки есть
`status_code`, `ozon_message` (ключ вырезан), `path`; у `OzonAuthError` — `kind`
(invalid / deactivated / no_role / ip_restricted / forbidden, по текстам Ozon);
«Circle is open» — `OzonServerError` без повторов. Rate limiter — скользящее окно в
Redis (sorted set + Lua, атомарно для API и всех воркеров), без Redis — в памяти. `get_ozon_client(user_id, db)` берёт активный
неистёкший ключ (последний использованный), обновляет `last_used_at`, клиент закрывать
(`async with`). Проверка ключа из 0.4 (`services/ozon_auth.py`) работает через тот же
клиент без повторов. Тесты лимитера против реального Redis: `TEST_REDIS_URL=redis://… pytest`.

Детали сделанного в 1.2: миграция 0003 — `products` (уникальность user_id+ozon_product_id),
`product_prices` (снепшоты) и `sync_runs` (журнал запусков: queued → running →
success/failed; нужен для статуса и защиты от двойного запуска, зависший > 30 мин
считается проваленным). `services/sync/products.py`: `/v3/product/list` (last_id,
видимости ALL и ARCHIVED) → батчами по 1000 `/v3/product/info/list` (карточка) и
`/v5/product/info/prices` (cursor); снепшот цены — только при изменении полей.
`services/sync/runner.py: run_sync(kind, …)` — общий запуск с журналом, все ошибки
пишутся в `sync_runs.error`, наружу не летят; новые виды синхронизации добавляются
в `JOBS`. Celery (`app/tasks/sync.py`): `sync_products_for_user`, `sync_products_all`
(Beat, каждый час в :07) ставит отдельную задачу на каждого пользователя с
действующим ключом. В задачах свой движок БД с NullPool (новый event loop на запуск).
В Docker Compose добавлен сервис `beat`. API: `POST /api/v1/sync/products` (202,
409 если уже идёт, 503 если брокер недоступен), `GET /api/v1/sync/status`.
Фронт: `components/SyncStatus.tsx` в шапке кабинета. Тестовые ответы Ozon —
`backend/tests/fixtures/ozon/*.json` (только `total_items`). Разбор полей сверен с
документацией — таблица в `docs/OZON_API.md` («Как мы читаем ответы»).

Детали сделанного в 1.3 (решения согласованы с владельцем): миграция 0004 — `warehouses`
(FBO | FBS | rFBS, кластер для FBO, уникальность user_id+ozon_warehouse_id) и
`product_stocks` (снепшот только при изменении; пара пропала из ответа — нулевой снепшот).
`/v1/warehouse/list` из промпта отключён Ozon, `/v4/product/info/stocks` не делит
остаток по складам, поэтому: FBO — `/v1/analytics/stocks` (как отчёт «Управление
остатками», по 100 SKU, SKU из товаров шага 1.2; present = «Доступно к продаже»,
in_transit = «В поставках в пути», reserved = NULL), FBS/rFBS — `/v2/warehouse/list` +
`/v1/product/info/warehouse/stocks`. Расчёт — `app/engine/stocks.py` (critical < 7,
low < 14, excess > 60 дней). Продаж пока нет: `services/stocks.avg_daily_sales` пустой
до шага 1.4, поэтому days_of_supply и статус = null («нет данных»), а не 0 — Ozon-овские
`ads`/`idc` намеренно не используем. Celery: `sync_stocks_all` в :13 и :43. API:
`GET /api/v1/warehouses`, `GET /api/v1/stocks` (warehouse_id, product_id,
status=critical|low|normal|excess|no_data, include_empty), `POST /api/v1/sync/stocks`.
Общий код синхронизаций: `runner.JOBS`, `tasks.sync.FOR_USER_TASKS`, `api/v1/sync._sync_now`;
разбор ответов — `services/sync/parsing.py`.

Детали сделанного в 1.4 (схема согласована с владельцем): миграция 0005 — `accrual_types`
(справочник Ozon, общий; `category` income/expense — по знаку первой ненулевой суммы, Ozon
её не отдаёт), `accruals` (+ sku, quantity, sync_scope, line_no) и `accrual_days`
(загруженные дни → `get_missing_periods`). Методы — бета: `/v1/finance/accrual/types`,
`/by-day` (ОДИН день, пагинация last_id с тем же date, не раньше 2022-01-01) и
`/postings` (по 200 номеров). Записи by-day с товарами → номер отправления (`unit_number`)
→ строки из `/postings` (товар — по SKU строки); без товаров (подписка, хранение) — из
by-day; отправление, которого `/postings` не вернул, — из `item_fees` by-day
(`raw_json.fallback`). Дубли: строки пишутся областями `posting:<номер>` / `day:<дата>`
(замена целиком + уникальный индекс user_id+source+sync_scope+line_no). Каждый день —
своя транзакция. `days_to_sync`: без периода — вчера, сегодня и незагруженные дни за
90 дней; с периодом — он заново (≤ 92 дней). Даты — по Москве (UTC+3). Celery:
`sync_finance_all` каждые 6 ч (:23), загрузка за 90 дней ставится при добавлении ключа
(`credentials._start_finance_backfill`, сбой очереди не мешает сохранить ключ).
`runner.run_sync(..., params)` передаёт период в задачу. API: `GET /api/v1/finance/accruals`
(период ≤ 366 дней, type_id, product_id, posting_number, limit/offset, итоги по фильтру),
`GET /api/v1/finance/accruals/by-day` (+ missing_periods), `POST /api/v1/finance/sync`;
в ответах `data_source: "api"` и предупреждение о расхождении с кабинетом.
`services/stocks.avg_daily_sales` к начислениям пока не подключён — продажи из API есть по
товарам, но не по складам; подключается на шаге 3.5 (продажи по дням).

Детали сделанного в 1.5: миграция 0006 — `promotions` (+ action_type, discount_value,
auto_add_dates, whole_stock) и `promotion_products` (+ price, max_action_price, stock,
add_mode, first_seen_at; участники is_active=true и кандидаты false). `/v1/actions/products`
и `/candidates` из промпта Ozon отключает 13.10.2026 — используются `/v2/actions/products`,
`/v2/actions/candidates` (last_id, limit ≤ 100) и `/v2/actions/auto-add/products/list`;
список акций — GET `/v1/actions`. `added_automatically`: add_mode SELLER/MANUAL → нет,
с AUTO → да; NOT_SET/неизвестно → да, если товар в автодобавлении на наступившую дату
или уже был отмечен (перечня add_mode в документации нет, сырое значение хранится).
`whole_stock` («Эластичный бустинг», «Скидка на сток», BUSINESS_LOGIC §9) — по названию и
типу акции; в API предупреждение и наш остаток товара (`stock_on_hand`). Маржа —
`app/engine/promotions.py` (простая: цена − комиссия − эквайринг − себестоимость × 1,22);
себестоимости в БД нет до шага 2.3 (`services/costs.cost_prices` пустой) → margin null и
пояснение. Min-цена: `min_seller_price`, иначе мин. цена товара; 0 = «не задана» → null.
Celery: `sync_promotions_all` каждые 3 ч (:33). API: `GET /api/v1/promotions`
(include_finished), `/{id}/products` (participating), `/auto-added`, `POST /api/v1/sync/promotions`.
Выход товара из акции пока удаляет строку участия — история появится на шаге 4.3.

Детали сделанного в 2.1 (`app/integrations/xlsx/`): `parse_report(content, costs, filename)` →
`schemas/report.NormalizedReportData` (+ sheet_name, header_row с 1, rows_count; суммы — Decimal,
в JSON строкой). `common.py`: чтение книги (openpyxl read_only + data_only, `reset_dimensions`,
.xls распознаётся по сигнатуре), `norm` (переносы/NBSP/пробелы, нижний регистр, ё→е),
`parse_number` (пробелы, ₽/руб., запятая/точка, «−», скобки; пусто → None; нечитаемое → None и
предупреждение с номером строки; > 20 таких — сворачиваются), `extract_period`. `detector.detect`
— сигналы и уверенность как в эталоне (60 строк каждого листа), «показной» ищется и выше заголовка,
имя файла — только при равной уверенности. Парсеры `parsers/accrual.py`, `parsers/realization.py`
— логика `reference/ozon_calculator.html`; учёт товаров и себестоимости — `parsers/base.SalesLedger`
(неизвестный артикул → missing_cost, cost=None, в cost_sales не входит, предупреждение; строки без
артикула — предупреждение). Отличия от эталона (баги/ручные данные): период реализации — из
«…за период с … по …», а не из «Отчет … от <даты>»; строки начислений без «Тип начисления» (ручные
пометки под таблицей) в auto_total не входят — предупреждение; итог реализации берётся и при ≤ 0;
строка нумерации столбцов не считается товаром. Нет «Всего выплат от партнёров» — предупреждение
и 0 (как в эталоне), нет «Итого реализовано» — сумма строк и предупреждение. Себестоимость —
`integrations/xlsx/costs.CostProvider` (`cost_of(article)`); сейчас `services/costs.get_cost_provider`
читает `COST_PRICES_CSV` (по умолчанию `reference/cost_prices.csv`, в Docker смонтирован в
`/reference`); на шаге 2.3 меняется только эта зависимость. API: `POST /api/v1/reports/parse`
(multipart `file`, ≤ `REPORT_MAX_SIZE_MB` → иначе 413, ошибки разбора — 400 с русским
текстом), без сохранения. P&L (owed, налог, net) — шаг 3.1.

Детали сделанного в 2.2 (решения согласованы с владельцем: без остатка и маржи до шага 3.1;
удаление — скрытие): миграция 0007 — `reports` (тип, период, status ready|error|processing,
summary_json = NormalizedReportData, manual_total, vat_multiplier 1.22, tax_rate 0.20, sha256 и
размер файла, uploaded_at/parsed_at, deleted_at, replaced_by_id), `report_files` (исходный файл
отдельно от списков); в `accruals`: `accrual_type_id` теперь nullable, + `type_name`, `offer_id`,
`report_id` (строки XLSX: source='xlsx', область `report:<id>`, line_no = строка Excel, raw_json —
вся строка по заголовкам; тип справочника Ozon и товар — если нашлись по названию / артикулу или
SKU; без даты — дата начала периода). Парсер: `parse_report_full` → `ParsedReport(data,
accrual_lines)`; сумма строк = auto_total. Сервис `services/reports.py`: конфликт — видимый отчёт
с тем же sha256 или того же типа с пересекающимся периодом; `on_conflict` ask (409 + conflicts) |
replace (старые скрываются, `replaced_by_id`) | keep. Скрытие удаляет строки accruals отчёта
(файл и запись остаются), восстановление и перечитывание пересобирают их из файла; перечитывание
не удалось — итоги прежние, status=error + error_message. Ошибка разбора при загрузке — 400,
ничего не сохраняется. API `/api/v1/reports`: POST `/upload` (только .xlsx, ≤ 10 МБ, 201),
GET (report_type, limit/offset → items+total), GET/PATCH/DELETE `/{id}` (PATCH: не переданное
не меняется, null — к авто/умолчаниям; DELETE — 204, скрытие), POST `/{id}/restore`,
`/{id}/reparse`; `/parse` остался. Начисления из XLSX в `/api/v1/finance/*` (только API) не
попадают; выбор источника для P&L — шаг 3.2. Фронт: `/dashboard/reports` (вкладки «Недельные» /
«Месячные», карточки: продано, пришло от Ozon, себестоимость, бейджи; удаление с «Отменить»),
`/dashboard/reports/upload` (drag&drop, прогресс, диалог «Заменить / Оставить оба»,
предпросмотр), `/dashboard/reports/[id]` (товары, начисления по типам, «Перечитать файл»);
общие компоненты — `components/reports.tsx`. Ручная правка «сколько пришло» в интерфейсе — шаг 3.3
(бэкенд PATCH готов). nginx: `client_max_body_size 11m`.

Детали сделанного в 0.5: миграция 0008 — `user_settings` (theme dark|light|system, vat_multiplier
1.22, tax_base profit|revenue, tax_rate 0.20; нет строки — значения по умолчанию,
`services/user_settings.get_settings`), `users.tokens_valid_after`, `reports.tax_base`. API:
GET/PATCH `/api/v1/settings` (не переданное не меняется), PATCH `/api/v1/auth/me` (имя),
POST `/api/v1/auth/change-password` (204; неверный текущий / совпадает с текущим — 400) —
ставит `tokens_valid_after`, токены, выпущенные раньше (`iat_ms` в JWT, точность до мс), не
принимаются в `get_current_user` и `/auth/refresh` — выход на всех устройствах. Новые отчёты
берут НДС и налог из настроек, PATCH отчёта с null — значения из настроек. Фронт: токены темы —
CSS-переменные в `globals.css` (`:root` тёмная, `[data-theme=light]`), Tailwind через
`rgb(var(--…) / <alpha-value>)`; тема ставится скриптом в `<head>` из localStorage (без вспышки),
`contexts/ThemeContext.tsx` после входа берёт тему из аккаунта, «как в системе» следит за
`prefers-color-scheme`. Меню — `src/lib/nav.ts` (NAV, ACCOUNT_MENU; пункты будущих шагов перечислены
в комментарии) и `components/AppHeader.tsx`: выпадающие списки по наведению мышью (закрытие с
задержкой 150 мс), нажатием на сенсорных экранах, с клавиатуры (↓, ↑, Esc); меню аккаунта
(инициалы ▾): аккаунт, API-ключи, основные настройки, выйти; бургер на узком экране. Страницы
`/dashboard/settings` (тема, расчёты по умолчанию) и `/dashboard/settings/account` (имя, смена
пароля → `/login?changed=1`). Вкладка акций — в адресе (`/dashboard/promotions?tab=auto`).

## Где что искать

- Описание проекта целиком → `docs/PROJECT.md`
- Формулы P&L, себестоимость, минимальная цена, поставки, аномалии, эталонные цифры
  для тестов → `docs/BUSINESS_LOGIC.md`
- Эндпоинты Ozon Seller API, лимиты, устаревшие методы → `docs/OZON_API.md`
- Официальная документация Ozon Seller API (выгрузка, октябрь 2026) → `docs/ozon/seller_api_2026-10.txt`
- История данных: что храним, откуда, как не потерять, бэкапы → `docs/DATA_HISTORY.md`
- Деньги: банки, ДДС, платёжный календарь, главный дашборд, что умеет КУБ24 → `docs/MONEY.md`
- План по фазам и шагам → `docs/ROADMAP.md`
- Готовые промпты на каждый шаг → `docs/prompts/`
- Рабочий JS-калькулятор P&L (логика парсинга двух типов отчётов) →
  `reference/ozon_calculator.html`
- Себестоимости 78 артикулов → `reference/cost_prices.csv`
- Образцовые отчёты Ozon → `backend/tests/fixtures/`
