# Ozon Analytics

SaaS-сервис аналитики для продавцов Ozon.

**С чего начать:** `docs/PROJECT.md` — описание проекта, `КАК_НАЧАТЬ.md` — как вести разработку в Claude Code.
Стек: FastAPI · Next.js 14 · PostgreSQL 16 · Celery + Redis · Docker.

## Запуск

```bash
cp .env.example .env          # задать SECRET_KEY и пароли
docker compose up --build
```

Миграции применяются автоматически при старте контейнера `api`. При разработке регистрация открыта
(`REGISTRATION_MODE=open` в `.env`).

**Сервер (прод):** `README-DEPLOY.md` — какой сервер выбрать, домен, установка (`scripts/setup.sh`), первый пользователь
(`scripts/admin.sh`), бэкапы, обновление (`scripts/deploy.sh`), что делать, если сайт не открывается.

| Сервис | Адрес |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Документация API (Swagger, только при разработке) | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

## Авторизация (шаг 0.2)

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/v1/auth/register` | Регистрация `{email, password, full_name}` → 201 |
| POST | `/api/v1/auth/login` | `{email, password}` → access (24 ч) + refresh (30 дн) |
| POST | `/api/v1/auth/refresh` | `{refresh_token}` → новая пара; старый refresh отзывается |
| POST | `/api/v1/auth/logout` | Отзывает текущий access и (опционально) refresh |
| GET  | `/api/v1/auth/me` | Текущий пользователь |

Пароли хранятся только в виде bcrypt-хеша. Отозванные токены лежат в Redis с TTL до их истечения.

Формат ошибок: `{"detail": "...", "code": 400|401|403|404}`.
400 — неверные данные или email занят, 401 — нет/неверный/отозванный токен или неверный пароль, 403 — аккаунт деактивирован.

### Проверка через curl

```bash
curl -X POST localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"me@shop.ru","password":"password1","full_name":"Иван"}'

curl -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"me@shop.ru","password":"password1"}'

curl localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access_token>"
```

## Тесты

```bash
docker compose exec api pip install -r requirements-dev.txt
docker compose exec api pytest
```

Тесты используют SQLite в памяти — Postgres и Redis для них не нужны.

## Полезные команды

```bash
docker compose exec api alembic revision --autogenerate -m "описание"
docker compose exec api alembic upgrade head
docker compose logs -f api
```
