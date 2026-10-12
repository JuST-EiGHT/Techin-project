# Ozon Seller API — что используем

> Актуально на осень 2026. Ozon регулярно выводит методы из эксплуатации —
> перед реализацией каждой интеграции сверяйся с https://docs.ozon.ru/api/seller/
> и при расхождении обнови этот файл.
>
> Последнее обновление — 12.10.2026: рассылка Ozon об отказе от поля `total`,
> новых методах этикеток FBS и параметре `scanit` (см. «Изменения Ozon» ниже).

## Авторизация

- Базовый URL: `https://api-seller.ozon.ru`
- Заголовки: `Client-Id: <id кабинета>`, `Api-Key: <ключ>`
- Ключи создаются в seller.ozon.ru → Настройки → API-ключи, у ключа есть набор прав
  (чтение товаров, цены, финансы и т.д.). С конца 2025 ключ живёт **6 месяцев** —
  хранить `expires_at` и предупреждать заранее.
- Для чтения хватает ключа с правами только на чтение. Изменение цен — отдельный
  ключ/права с явного согласия пользователя.

## Лимиты

- Не более **30 000 операций с товарами в минуту** (операция = один товар в запросе).
- При превышении — `429`. Если в запросе товаров больше, чем осталось лимита,
  **отклоняется весь запрос**, а не часть. Отправлять батчами, вести счётчик в Redis,
  при 429 — экспоненциальная пауза (1, 2, 4 … 30 сек).
- Реализовано в `app/integrations/ozon/` (шаг 1.1): счётчик — скользящее окно 60 с
  на Client-Id; передавай в `OzonClient.request(..., items=N)` число товаров в запросе.
  Какие методы Ozon считает «операциями с товарами» и как именно (по товарам в запросе
  или в ответе) — по документации не сверено; уточнить при первых 429.

## Чтение

| Назначение | Метод | Эндпоинт |
|---|---|---|
| Список товаров (пагинация `last_id`) | POST | `/v3/product/list` |
| Детали товаров | POST | `/v3/product/info/list` |
| Цены, комиссии, индекс цены, акционная цена | POST | `/v5/product/info/prices` |
| Остатки по складам | POST | `/v4/product/info/stocks` |
| Склады продавца | POST | `/v1/warehouse/list` |
| Отправления FBO / FBS | POST | `/v2/posting/fbo/list`, `/v4/posting/fbs/list` |
| Начисления по отправлениям | POST | `/v1/finance/accrual/postings` |
| Справочник типов начислений | POST | `/v1/finance/accrual/types` |
| Начисления по дням | POST | `/v1/finance/accrual/by-day` |
| Отчёт о реализации за месяц | POST | `/v2/finance/realization` |
| Аналитика (выручка, заказы) | POST | `/v1/analytics/data` |
| Остатки и оборачиваемость | POST | `/v1/analytics/stocks` |
| Акции | GET | `/v1/actions` |
| Товары в акции / кандидаты | POST | `/v1/actions/products`, `/v1/actions/candidates` |

Финансовые методы отдают максимум **1 месяц за запрос** — длинный период дробить.

`/v5/product/info/prices` — главный метод для экономики товара: `price`,
`min_price`, `old_price`, `marketing_seller_price`, комиссии по схемам, эквайринг,
`auto_action_enabled`, индексы цены.

## Изменения Ozon, которые нужно учитывать

Источник: рассылка Ozon «Обновления в Seller API» (октябрь 2026) и
[статья для разработчиков](https://dev.ozon.ru/news/800-Podderzhka-novoi-etiketki-scanit-i-migratsiia-metodov-v-Seller-API/).

### Поле `total` → `total_items` (старое отключат 23 ноября 2026)

В методах `/v3/product/list`, `/v4/product/info/stocks`, `/v5/product/info/prices`
читать **только `total_items`**, поле `total` не использовать нигде — ни в коде, ни в
тестовых ответах-фикстурах. Пагинацию строить на `last_id` / курсоре, а не на счётчике.

### Этикетки FBS (старые методы отключат 2 ноября 2026)

Магазин работает по FBO, этикетки сейчас не нужны. Если понадобится FBS — только новые методы:

| Было (не использовать) | Стало |
|---|---|
| `/v1/posting/fbs/package-label/get` | `/v2/posting/fbs/package-label/get` |
| `/v1/posting/fbs/package-label/create` | `/v3/posting/fbs/package-label/create` |
| `/v2/posting/fbs/package-label/create` | `/v3/posting/fbs/package-label/create` |
| `/v2/posting/fbs/package-label` | `/v3/posting/fbs/package-label/create` + `/v2/posting/fbs/package-label/get` |

С 5 октября 2026 старые методы до отключения уже отдают новые этикетки, если они доступны.

### Параметр `scanit` (штрихкод на новых этикетках FBS)

Добавлен в ответы `/v3/posting/fbs/get`, `/v4/posting/fbs/list`,
`/v4/posting/fbs/unfulfilled/list`; `/v2/posting/fbs/get-by-barcode` теперь
принимает `scanit` и в запросе. При сохранении FBS-отправлений хранить `scanit`
рядом со штрихкодом. Для списка FBS-отправлений брать `/v4/posting/fbs/list` — эта версия
упомянута в рассылке; отключён ли `/v3`, Ozon не пишет — сверить по документации.

## Изменение (только через систему безопасности, см. BUSINESS_LOGIC.md §8)

| Назначение | Метод | Эндпоинт |
|---|---|---|
| Цена, мин. цена, зачёркнутая, автоакции | POST | `/v1/product/import/prices` |
| Добавить товар в акцию | POST | `/v1/actions/products/activate` |
| Убрать товар из акции | POST | `/v1/actions/products/deactivate` |
| Остатки FBS | POST | `/v2/products/stocks` |

## Устаревшее — не использовать

- `/v3/finance/transaction/list` — отключён 6 июля 2026
- поле `total` в `/v3/product/list`, `/v4/product/info/stocks`, `/v5/product/info/prices` —
  отключат 23 ноября 2026, использовать `total_items`
- `/v1/posting/fbs/package-label/get`, `/v1/posting/fbs/package-label/create`,
  `/v2/posting/fbs/package-label/create`, `/v2/posting/fbs/package-label` — отключат 2 ноября 2026
- `/v2/product/list`, `/v4/product/info/prices`, `/v3/product/info/stocks` — заменены новыми версиями

## Чего API не даёт

- Историю цен — придётся копить снепшоты у себя (таблица `product_prices`).
- Себестоимость — только из нашего справочника.
- Точные финансы «как в личном кабинете» — у API есть оговорка о расхождениях,
  поэтому для итогового P&L главный источник — загруженный XLSX.
- Рекламные расходы детально — это отдельный **Ozon Performance API**
  (свои ключи, OAuth client_credentials). В Seller API реклама видна только
  агрегатом в начислениях.
