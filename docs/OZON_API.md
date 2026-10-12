# Ozon Seller API — что используем

> **Сверено с официальной документацией** — её выгрузка лежит в
> `docs/ozon/seller_api_2026-10.txt` (последние изменения в ней — 6 октября 2026).
> docs.ozon.ru из облачной среды разработки недоступен, поэтому перед каждой новой
> интеграцией ищи метод в этом файле (`grep -n -x "/v3/product/list" docs/ozon/*.txt`
> находит начало раздела метода). Владелец может прислать свежую выгрузку — тогда
> заменить файл и перепроверить этот документ.

## Авторизация

- Базовый URL: `https://api-seller.ozon.ru`
- Заголовки: `Client-Id: <id кабинета>`, `Api-Key: <ключ>`, `Content-Type: application/json`
- Ключ создаётся в кабинете: **Настройки → Seller API** → «Сгенерировать ключ»; выбирается
  уровень доступа (роли). Сам ключ виден только при создании.
- **Срок действия ключа — 3 месяца.** Точную дату отдаёт `POST /v1/roles` → `expires_at`
  (вместе со списком ролей и методов ключа). Мы берём срок оттуда при добавлении и при
  каждой проверке ключа; если Ozon срок не сообщил — +3 месяца.
- Ключ можно ограничить IP-адресами — тогда запросы с других адресов отклоняются
  (`Api-Key is restricted to specific IP addresses`). При деплое сообщить пользователю IP сервиса.
- Ozon может отозвать ключ при подозрительной активности или слишком большой нагрузке.
- Есть и OAuth-токены (для приложений) — не используем.

## Лимиты

- **Не больше 50 запросов в секунду на все методы с одного Client ID** (с 22.05.2025; раньше 10).
- У части методов свой лимит — он указан в описании метода:

  | Метод | Лимит |
  |---|---|
  | `/v2/products/stocks` | 80 запросов в минуту; 100 пар товар-склад за запрос; одну пару — не чаще раза в 30 с |
  | `/v1/product/import/prices` | цену каждого товара — не больше 10 раз в час |
  | `/v1/analytics/data` | без Premium Plus: 50 запросов в сутки, данные за последние 3 месяца |
  | `/v1/product/placement-zone/info` | 10 запросов в секунду |

- При превышении — `429 Too Many Requests` и заголовок `Retry-After` (через сколько секунд
  повторить). В ответах есть `Ratelimit-Remaining` — сколько запросов осталось.
- Много одинаковых или ошибочных запросов → Ozon может ограничить доступ без предупреждения.
- Реализация: `app/integrations/ozon/rate_limiter.py` — скользящее окно в Redis на Client ID
  (50 в секунду) плюс `METHOD_LIMITS` для методов со своим лимитом. **Подключая новый метод
  со своим лимитом — добавь его в `METHOD_LIMITS`.**
- Ограничения «30 000 операций с товарами в минуту» в документации нет — было ошибкой
  исходного документа, исправлено.

## Частые ошибки (все методы)

| Текст ошибки Ozon | Что значит | Как обрабатываем (`OzonAuthError.kind` и т.п.) |
|---|---|---|
| `Invalid Api-Key, please check the key and try again` | неверный ключ | `invalid` |
| `Api-key is deactivated, use another one or generate a new one` | ключ деактивирован | `deactivated` |
| `Api-Key is missing a required role for a method` | у ключа нет роли для метода | `no_role` |
| `Api-Key is restricted to specific IP addresses` | ключ разрешён только с определённых IP | `ip_restricted` |
| `You have reached request rate limit per second` | превышен лимит 50/с или лимит метода | 429 → повтор по `Retry-After` |
| `Circle is open` | Ozon временно заблокировал метод из-за нагрузки (на несколько минут) | `OzonServerError` без повторов |
| `Internal error` | сервер не успел обработать запрос | 5xx → повтор |
| `method is not allowed` | метода нет / неверный тип запроса | `OzonAPIError` |

HTTP-коды для этих текстов в документации не указаны, поэтому клиент распознаёт их по тексту.
Тело ошибки: `{"code": <int>, "message": "<текст>", "details": [...]}`.

## Чтение

| Назначение | Метод | Эндпоинт | Важное |
|---|---|---|---|
| Ключ: роли и срок | POST | `/v1/roles` | `expires_at`, `roles[].methods` |
| Список товаров | POST | `/v3/product/list` | `limit` 1–1000, пагинация `last_id`; `filter.visibility`: `ALL` — все **кроме архивных**, `ARCHIVED` — архив; в элементе есть `sku` |
| Карточки товаров | POST | `/v3/product/info/list` | до 1000 идентификаторов за запрос; `product_id` — массив **строк** int64; ответ `{"items": [...]}` |
| Цены, комиссии, индексы | POST | `/v5/product/info/prices` | `limit` 1–1000 (обязателен), `filter` обязателен, пагинация `cursor` |
| Остатки (FBO, FBS, rFBS, FBP) | POST | `/v4/product/info/stocks` | `limit` до 1000, `cursor` |
| Остатки FBO по кластерам/складам, продажи за 28 дней | POST | `/v1/analytics/stocks` | раздел «FBO → Управление остатками»; только одно из `cluster_ids` / `macrolocal_cluster_ids`; кластеры — `/v1/cluster/list` |
| Склады FBS и rFBS | POST | `/v2/warehouse/list` | `limit` ≤ 200, `cursor`; FBO-складов в нём нет |
| Отправления FBO | POST | `/v3/posting/fbo/list` | период не больше года |
| Отправления FBS | POST | `/v4/posting/fbs/list` | |
| Начисления по отправлениям | POST | `/v1/finance/accrual/postings` | `posting_numbers` 1–200 |
| Справочник типов начислений | POST | `/v1/finance/accrual/types` | |
| Начисления за день | POST | `/v1/finance/accrual/by-day` | `last_id` = `date` из предыдущего ответа |
| Отчёт о реализации за месяц | POST | `/v2/finance/realization` | не раньше августа 2023; приходит не позднее 5-го числа следующего месяца |
| Аналитика (выручка, заказы) | POST | `/v1/analytics/data` | лимит 50/сутки без Premium Plus |
| Акции Ozon | GET | `/v1/actions` | |
| Товары в акции / кандидаты | POST | `/v2/actions/products`, `/v2/actions/candidates` | v1 отключаются 13.10.2026 |

Финансовые данные API могут расходиться с личным кабинетом — для P&L главный источник XLSX
(см. `BUSINESS_LOGIC.md` §9).

## Как мы читаем ответы (шаг 1.2)

| Наше поле | Откуда | Примечание |
|---|---|---|
| список товаров | `/v3/product/list`, `visibility` = `ALL`, затем `ARCHIVED` | по документации `ALL` не включает архив |
| `ozon_sku` | `/v3/product/info/list` → `sku`, иначе `sku` из списка товаров, иначе `sources[].sku` | |
| `barcode` | `barcodes[0]` | |
| `category_id` | `description_category_id` | |
| `vat` | `vat` | бывает `"0.1"`, `0.2` и `"20%"` — приводим к доле |
| `is_archived` | `is_archived` (вручную) или `is_autoarchived` (автоматически) | |
| `price`, `min_price`, `old_price` | `/v5/product/info/prices` → `price.*` | 0 у Ozon = «не задано», храним как есть |
| `marketing_price` | `price.marketing_seller_price` | `price.marketing_price` Ozon удалил 12.11.2025; описание поля в выгрузке свёрнуто |
| `commission_pct` | `commissions.sales_percent_fbo` | магазин FBO; для FBS-продавцов есть `sales_percent_fbs` |
| `acquiring_pct` | `acquiring` | описание свёрнуто; в примерах 1.5 и 1.8 при цене ~3000 и ~6000 — это **процент**, не рубли |
| `price_index` | `price_indexes.color_index` | числовые индексы — `ozon_index_data.price_index_value` и др. |

Пригодится позже: `price.net_price` — себестоимость, которую продавец может передать в Ozon
через `/v1/product/import/prices` (`prices.net_price`); `/v1/product/prices/details` — цена
для покупателя (`customer_price`), но только с подпиской Premium Pro.

## Изменение (только через систему безопасности, см. BUSINESS_LOGIC.md §8)

| Назначение | Метод | Эндпоинт | Важное |
|---|---|---|---|
| Цена, мин. цена, зачёркнутая | POST | `/v1/product/import/prices` | цену товара — не больше 10 раз в час; `prices.auto_action_enabled` устарел |
| Добавить или обновить товар в акции | POST | `/v1/actions/products/update` | заменяет `/v1/actions/products/activate` |
| Убрать товар из акции | POST | `/v2/actions/products/deactivate` | заменяет `/v1/actions/products/deactivate` |
| Остатки FBS | POST | `/v2/products/stocks` | 100 пар за запрос, 80 запросов/мин, пара — раз в 30 с |

Все методы Ozon — POST: повтор после таймаута может применить изменение дважды. Для
изменяющих запросов в фазе 6 решить, где нужен `OzonClient(..., retry=False)`.

## Изменения Ozon, которые нужно учитывать

### Поле `total` → `total_items` (старое отключат 23 ноября 2026)

В `/v3/product/list`, `/v4/product/info/stocks`, `/v5/product/info/prices` читать **только
`total_items`**; `total` не использовать нигде — ни в коде, ни в тестовых фикстурах.
Пагинацию строить на `last_id` / `cursor`, а не на счётчике.

### Этикетки FBS (старые методы отключат 2 ноября 2026)

Магазин работает по FBO, этикетки сейчас не нужны. Если понадобится FBS — только новые методы:

| Было (не использовать) | Стало |
|---|---|
| `/v1/posting/fbs/package-label/get` | `/v2/posting/fbs/package-label/get` |
| `/v1/posting/fbs/package-label/create` | `/v3/posting/fbs/package-label/create` |
| `/v2/posting/fbs/package-label/create` | `/v3/posting/fbs/package-label/create` |
| `/v2/posting/fbs/package-label` | `/v3/posting/fbs/package-label/create` + `/v2/posting/fbs/package-label/get` |

### Параметр `scanit` (штрихкод на новых этикетках FBS)

Есть в ответах `/v3/posting/fbs/get`, `/v4/posting/fbs/list`, `/v4/posting/fbs/unfulfilled/list`;
`/v2/posting/fbs/get-by-barcode` принимает `scanit` и в запросе. При сохранении
FBS-отправлений хранить `scanit` рядом со штрихкодом.

Источник: рассылка Ozon «Обновления в Seller API» (октябрь 2026) и
[статья для разработчиков](https://dev.ozon.ru/news/800-Podderzhka-novoi-etiketki-scanit-i-migratsiia-metodov-v-Seller-API/).

## Устаревшее — не использовать

| Метод / поле | Статус | Замена |
|---|---|---|
| `/v3/finance/transaction/list`, `/v3/finance/transaction/totals` | отключены 8.09.2026 | `/v1/finance/accrual/postings`, `/types`, `/by-day` |
| `/v1/warehouse/list` | отключён 7.04.2026 | `/v2/warehouse/list` |
| `/v2/posting/fbo/list` | отключён 31.08.2026 | `/v3/posting/fbo/list` |
| `/v3/posting/fbs/list` | отключён 31.08.2026 | `/v4/posting/fbs/list` |
| `/v1/actions/products`, `/v1/actions/candidates` | отключаются 13.10.2026 | `/v2/actions/products`, `/v2/actions/candidates` |
| `/v1/actions/products/activate` | отключается 13.10.2026 | `/v1/actions/products/update` |
| `/v1/actions/products/deactivate` | отключается 13.10.2026 | `/v2/actions/products/deactivate` |
| `/v1/actions/auto-add/products/*` | отключаются 13.10.2026 | `/v2/actions/auto-add/products/*` |
| `/v1/analytics/manage/stocks` | отключён 22.01.2026 | `/v1/analytics/stocks` |
| `/v1/product/info/stocks-by-warehouse/fbs` | отключён 7.04.2026 | `/v2/product/info/stocks-by-warehouse/fbs` |
| поле `total` в `/v3/product/list`, `/v4/product/info/stocks`, `/v5/product/info/prices` | отключат 23.11.2026 | `total_items` |
| `price.marketing_price` (`/v5/product/info/prices`), `items.marketing_price` (`/v3/product/info/list`) | удалены 12.11.2025 | `price.marketing_seller_price` |
| методы этикеток FBS v1/v2 | отключат 2.11.2026 | см. таблицу выше |
| `/v2/product/list`, `/v4/product/info/prices`, `/v3/product/info/stocks` | заменены | `/v3/product/list`, `/v5/product/info/prices`, `/v4/product/info/stocks` |

## Чего API не даёт

- Историю цен — копим снепшоты у себя (`product_prices`); в Ozon история только в кабинете.
- Себестоимость — только из нашего справочника (Ozon хранит `net_price`, если продавец его передал).
- Точные финансы «как в личном кабинете» — у API есть оговорка о расхождениях,
  поэтому для итогового P&L главный источник — загруженный XLSX.
- Рекламные расходы детально — это отдельный **Ozon Performance API**
  (свои ключи, OAuth client_credentials). В Seller API реклама видна только
  агрегатом в начислениях.
