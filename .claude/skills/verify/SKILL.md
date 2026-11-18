---
name: verify
description: Проверить изменение в работающем сервисе перед коммитом — поднять Postgres, Redis, API и фронтенд локально, заполнить тестовым магазином, прогнать сценарий в браузере (Playwright, обе темы, 390 px) или замерить запросы (Server-Timing). Использовать перед каждым коммитом, который меняет поведение, интерфейс или скорость; по правилам «Проверка изменений» в CLAUDE.md.
---

# verify — проверка изменения в работающем сервисе

Правила — `CLAUDE.md`, раздел «Проверка изменений». Здесь — как именно поднять сервис и проверить.

## 1. Что проверять — выбрать по diff

| Изменение | Проверка |
|---|---|
| Расчёт (engine/, services/) | тесты модуля + сверка с эталонами `docs/BUSINESS_LOGIC.md`; инварианты — свойства (сумма строк = итог, нет данных ≠ 0, порядок не меняет итог) |
| Эндпоинт API | тест через `client`; вживую — `curl` к запущенному API, ответ и `Server-Timing` |
| Интерфейс | сценарий Playwright, который делает то, что сделает владелец: нажать, ввести, увидеть результат; светлая и тёмная тема, 390 px, без прокрутки страницы вбок, без ошибок в консоли |
| Скорость | `Server-Timing` (время, база, число SQL) до и после — на одинаковых данных |
| Переделка поведения (старый код → новый) | сравнить ответ старой и новой версии на одних и тех же данных: отличия — только задуманные, перечислить их |
| Миграция | `alembic upgrade head` на базе с данными + `tests/test_migrations_tree.py` |

## 2. Поднять сервис (облачная среда, ~2 минуты)

```bash
service postgresql start; service redis-server start
# база для проверки — один раз за машину
su postgres -c "psql -q -c \"create user ozon with password 'ozon_secret' createdb\"" 2>/dev/null
su postgres -c "psql -q -c 'create database ozon_e2e owner ozon'" 2>/dev/null

cd backend && python -m venv .venv && . .venv/bin/activate && pip install -q -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://ozon:ozon_secret@localhost:5432/ozon_e2e REDIS_URL=redis://localhost:6379/0
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") REGISTRATION_MODE=open
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
alembic upgrade head
nohup uvicorn app.main:app --port 8000 > /tmp/api.log 2>&1 &
PYTHONPATH=. python ../.claude/skills/verify/seed.py       # тестовый магазин: t1@example.com / password123

cd ../frontend && npm ci
API_URL=http://localhost:8000 nohup npx next dev -p 3000 > /tmp/web.log 2>&1 &
# скорость — только на рабочей сборке: npx next build && API_URL=… npx next start -p 3000
```

- После перезапуска машины Postgres и Redis останавливаются — снова `service … start`.
- Новые зависимости из main (ошибка «No module named …», «Can't resolve …») — заново `pip install -r` и `npm ci`.
- Тесты бэкенда запускать без этих переменных: `env -u DATABASE_URL -u REDIS_URL -u SECRET_KEY -u ENCRYPTION_KEY pytest`.

## 3. Браузер

Playwright уже установлен: `require('playwright')`, `chromium.launch()` без параметров. Вход:

```js
await page.goto('http://localhost:3000/login')
await page.fill('#email', 't1@example.com'); await page.fill('#password', 'password123')
await Promise.all([page.waitForURL(/dashboard/), page.click('button[type=submit]')])
await page.request.patch('http://localhost:3000/api/session/proxy/settings', { data: { theme: 'dark' } })  // тема
```

Один скрипт — все проверки: каждое утверждение печатает `OK` / `FAIL` с фактическим значением; `page.on('pageerror')`
собирает ошибки; в конце — `document.documentElement.scrollWidth <= innerWidth`. Скриншоты — только изменённых экранов.

## 4. Скорость

```bash
TOK=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"t1@example.com","password":"password123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
redis-cli flushall   # холодный кэш
curl -s -D - -o /dev/null "localhost:8000/api/v1/<путь>" -H "Authorization: Bearer $TOK" | grep -i server-timing
```

Сервер владельца в 5–8 раз медленнее этой машины (2 ГБ памяти): 0,2 с здесь ≈ 1–1,5 с там. На сервере медленные
запросы видны в журнале: `./scripts/compose.sh logs api | grep "медленный запрос"`.

## 5. Итог

В сообщении коммита — строка `Проверено:` с тем, что реально запускалось (тесты, сценарий в браузере, замер), или
`Проверено: не проверено — <почему>`. Владельцу — то же простыми словами.
