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
| Дерево категорий и типов (шаг 10.3) | POST | `/v1/description-category/tree` | `language` (`DEFAULT` — русский); весь каталог Ozon одним ответом: `result[]{description_category_id, category_name, disabled, children[]}`, на последнем уровне — типы `{type_id, type_name}` |
| Цены, комиссии, индексы | POST | `/v5/product/info/prices` | `limit` 1–1000 (обязателен), `filter` обязателен, пагинация `cursor` |
| Остатки (FBO, FBS, rFBS, FBP) | POST | `/v4/product/info/stocks` | `limit` до 1000, `cursor` |
| Остатки FBO по кластерам/складам, продажи за 28 дней | POST | `/v1/analytics/stocks` | раздел «FBO → Управление остатками»; только одно из `cluster_ids` / `macrolocal_cluster_ids`; кластеры — `/v1/cluster/list` |
| Склады FBS и rFBS | POST | `/v2/warehouse/list` | `limit` ≤ 200, `cursor`; FBO-складов в нём нет |
| Отправления FBO | POST | `/v3/posting/fbo/list` | период по дате заказа не больше года; `limit` ≤ 100, `cursor`; `with.analytics_data` — склад отгрузки, `with.financial_data` — `cluster_from` / `cluster_to` (шаг 5.2) |
| Отправления FBS и rFBS | POST | `/v4/posting/fbs/list` | `filter.since` / `to` (не больше года), `limit` ≤ 100, `cursor`; склад продавца — `delivery_method.warehouse_id`; в ответе персональные данные покупателя (шаг 5.2) |
| Начисления по отправлениям | POST | `/v1/finance/accrual/postings` | `posting_numbers` 1–200, только вида `123-456-7`; **не используем** (ниже) |
| Справочник типов начислений | POST | `/v1/finance/accrual/types` | |
| Начисления за день | POST | `/v1/finance/accrual/by-day` | `last_id` = `date` из предыдущего ответа |
| Баланс у Ozon: начислено, выплаты, остаток (шаг 3.6) | POST | `/v1/finance/balance` | **бета**, «Финансы → Баланс»; `date_from`, `date_to` (YYYY-MM-DD), период ≤ 30 дней; `total{opening_balance, accrued, payments[], closing_balance}`, `cashflows{sales, returns, services[]}`; **дат выплат нет** — запрашиваем по одному дню (`services/sync/ozon_balance.py`; предположение «выплаты за день = выплата этого дня» проверить на реальном ключе, сверка по полумесяцам покажет расхождение) |
| Финансовый отчёт по полумесяцам (шаг 3.6, сверка) | POST | `/v1/finance/cash-flow-statement/list` | только периоды 1–15 и 16–конец месяца; `date{from,to}`, `page`, `page_size`, `with_details`; `details{payments[], begin_balance_amount, end_balance_amount, …}` |
| Отчёт о реализации за месяц (шаг 10.12) | POST | `/v2/finance/realization` | `month`, `year`; не раньше августа 2023; приходит не позднее 5-го числа следующего месяца; ответ `result{header{start_date, stop_date, номер, реквизиты}, rows[]}`; строка — `item{offer_id, sku, name, barcode}`, `delivery_commission` (продажи) и `return_commission` (возвраты): `amount`, `quantity`, `bonus`, `bank_coinvestment`, `stars`, `pick_up_point_coinvestment`, `commission`, `compensation`, `standard_fee`, `total`, `price_per_instance`; описаний полей в выгрузке нет — итоги собираем сами (`integrations/ozon_reports/realization.py`), сверить с файлом из кабинета (8.4a). `/v1/finance/realization` устарел и удалён из документации |
| Позаказный отчёт о реализации | POST | `/v1/finance/realization/posting` | `month`, `year`; с августа 2023; **не используем** — продажи по отправлениям есть в начислениях |
| Отчёт о реализации за день | POST | `/v1/finance/realization/by-day` | `day`, `month`, `year`; не дальше 32 дней; **только с подпиской Premium Plus / Pro** — не используем |
| Отчёт о взаиморасчётах (шаг 10.12) | POST | `/v1/finance/mutual-settlement` | `date` (YYYY-MM), `language` RU / EN; ответ — `result.code`, файл — по `/v1/report/info`; «Финансы → Документы → Аналитические отчёты» |
| Отчёты о компенсациях и декомпенсациях (шаг 10.12) | POST | `/v1/finance/compensation`, `/v1/finance/decompensation` | `date` (YYYY-MM), `language`; ответ — `result.code`, файл — по `/v1/report/info`; «Финансы → Документы → Компенсации и прочие начисления» |
| Готовность отчёта по коду и файл (шаг 10.12) | POST | `/v1/report/info` | `code`; `result{status: waiting / processing / success / failed, file (ссылка на XLSX), error, report_type (MUTUAL_SETTLEMENT, COMPENSATION_REPORT, DECOMPENSATION_REPORT …), expires_at}`; код живёт 3 дня; файл скачиваем только с доменов `ozone.ru` / `ozon.ru` |
| Реестр продаж юрлицам | POST | `/v1/finance/document-b2b-sales`, `/json` | `date` (YYYY-MM); **не используем** — продажи есть в начислениях и реализации |
| Закрывающие документы за услуги Ozon (акты, УПД, счета-фактуры) | — | — | **через API нет** — только в личном кабинете (Финансы → Документы) |
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
| `category_id` | `description_category_id` | название — справочник категорий (ниже, шаг 10.3) |
| `vat` | `vat` | бывает `"0.1"`, `0.2` и `"20%"` — приводим к доле |
| `is_archived` | `is_archived` (вручную) или `is_autoarchived` (автоматически) | |
| `price`, `min_price`, `old_price` | `/v5/product/info/prices` → `price.*` | 0 у Ozon = «не задано», храним как есть |
| `marketing_price` | `price.marketing_seller_price` | `price.marketing_price` Ozon удалил 12.11.2025; описание поля в выгрузке свёрнуто |
| `commission_pct` | `commissions.sales_percent_fbo` | магазин FBO; для FBS-продавцов есть `sales_percent_fbs` |
| `acquiring` | `acquiring` | **рубли**, не процент: максимальная комиссия за эквайринг при текущей цене (описание в выгрузке свёрнуто; 1.5 и 1.8 в примерах — заглушки, у реальных товаров 19,8 при цене ~1300 ₽). Процент для расчётов — `acquiring / price × 100` (`engine.products.acquiring_pct`). До 20.10.2026 сервис считал это число процентом — исправлено, миграция 0030 |
| `price_index` | `price_indexes.color_index` | числовые индексы — `ozon_index_data.price_index_value` и др. |

### Остатки (шаг 1.3)

| Наше поле | FBO: `/v1/analytics/stocks` | FBS/rFBS: `/v1/product/info/warehouse/stocks` |
|---|---|---|
| склад | `warehouse_id`, `warehouse_name`, `cluster_id`, `cluster_name` | склад из `/v2/warehouse/list`: `warehouse_id`, `name`, `is_rfbs`, `status` (`created` = активный) |
| `present` | `available_stock_count` («Доступно к продаже») | `present` |
| `reserved` | нет в ответе → NULL | `reserved` |
| `in_transit` | `transit_stock_count` («В поставках в пути») | нет → NULL |

`/v1/analytics/stocks` требует `skus` (до 100, строками), без пагинации; одна пара SKU × склад
может прийти несколькими строками (разные теги/зоны) — складываем. Там же есть `ads`
(продажи в день за 28 дней), `idc` (дни запаса), `turnover_grade` — пока не используем:
дни запаса считаем по своим продажам (появятся на шаге 1.4). Сверено для шага 5.1 (выгрузка 2026-10): там же
`days_without_sales` / `days_without_sales_cluster`, `ads_cluster`, `idc_cluster`, `excess_stock_count` (излишки с поставки,
доступные к вывозу) — «Кандидаты на вывоз» считаем по своим продажам и не используем их. Создать заявку на вывоз через
Seller API нельзя: есть только отчёты по вывозу и утилизации FBO (`/v1/removal/from-supply/list`, `/v1/removal/from-stock/list`,
бета) — заявка делается в кабинете.

### Начисления (шаг 1.4; по реальным ответам — 20.10.2026)

Методы `/v1/finance/accrual/*` в документации помечены как **бета**, вложенные поля by-day свёрнуты в выгрузке. Формат
ниже сверен с ответами Ozon на магазине владельца и с его отчётом XLSX по начислениям (три отправления — копейка в
копейку). Сырой ответ храним в `accruals.raw_json`.

| Что | Откуда | Примечание |
|---|---|---|
| справочник типов | `/v1/finance/accrual/types` → `accrual_types[]{id, name, description}` | 135 типов; **`name` — английский код** (`SaleCommission`, `Logistic`), **`description` — русское название** («Вознаграждение за продажу», «Логистика» — как в отчёте XLSX); категории доход/расход нет — ставим по знаку сумм. **Типа «Выручка» в справочнике нет** |
| записи дня | `/v1/finance/accrual/by-day` (`date`, `last_id`) → `accruals[]{accrual_id, accrued_category, date, unit_number, total_amount, posting, item_fees, non_item_fee, container_fees}` | один день за запрос; `last_id` живёт 15 мин, с ним передаётся тот же `date`; незаполненные блоки — `null` |
| продажа и доставка по отправлению | `accrued_category: POSTING` → `posting{delivery_schema, products[{sku, quantity, commission{seller_price, sale_price, sale_amount, commission, sale_commission, bonus, coinvestment, commission_ratio}, delivery{services[{type_id, accrued}], total_accrued}}]}` | **«Выручка» = `sale_amount` − `bonus` − `coinvestment`** (= `sale_price` при 1 шт.), «Баллы за скидки» = `bonus`, «Программы партнёров» = `coinvestment`, «Вознаграждение за продажу» = `commission` (= `sale_commission`); `seller_price` = выручка + баллы + партнёры; `commission_ratio` — строка вида `value:"0.520000"`; у невыкупа поля `commission` — `null`, только доставка. Сумма строк записи = `total_amount` |
| начисления по товару | `ITEM` → `item_fees.fees[{sku, fees[{type_id, accrued}]}]` | эквайринг, «Звёздные товары», вывоз со склада… — отдельными записями с тем же `unit_number` |
| без товара | `NON_ITEM` → `non_item_fee{type_id, accrued}`; `container_fees.fees[]` | размещение, кросс-докинг, продвижение, доп. упаковка |
| `unit_number` | номер отправления (`12345678-0001-1`), заказа (`1234567890-0001`), поставки (`RU12-AB123456789`), услуги (`1234567890123`) | храним в `posting_number` — как «ID начисления» отчёта XLSX |
| строки отправления | `/v1/finance/accrual/postings` → `posting_accruals[].accruals[]{accrual_date, type_id, sku, quantity, seller_price, accrued}` | **не используем**: принимает только номера вида `\d+-\d+-\d+` (иначе 400 «value does not match regex pattern» — так падала первая загрузка), эквайринга в ответе нет, лимит в секунду маленький (429 уже на втором запросе); выручки типом нет и здесь (только `seller_price` у строки комиссии) |
| сумма | `accrued.amount`, `total_amount.amount` (строка) | знак как у Ozon: + доход, − расход |

### Акции (шаг 1.5)

| Что | Откуда | Примечание |
|---|---|---|
| список акций | GET `/v1/actions` → `result[]` | `id`, `title`, `action_type`, `date_start`/`date_end`, `freeze_date`, `auto_add_dates`, `is_participating`, `discount_type`/`discount_value` |
| товары в акции | `/v2/actions/products` (`action_id`, `last_id`, `limit` ≤ 100) → `products[]` | `id` = product_id; цены — объекты `{amount, currency}` (в v1 были числа); `add_mode` — кто добавил |
| кандидаты | `/v2/actions/candidates` (те же параметры) | |
| автодобавление | `/v2/actions/auto-add/products/list` (`action_id`, `auto_add_date`, `offset`, `limit` 1–100) | `product_id`, `quantity_to_auto_add` |
| мин. цена | `min_seller_price` | 0 = не задана |

Перечня значений `add_mode` и `action_type` в выгрузке нет (в примерах `SELLER`, `MANUAL`,
`NOT_SET`; `DISCOUNT`) — правила в `services/sync/promotions.py`, сырое `add_mode` хранится.
`/v1/actions` в документации не помечен устаревшим.

Пригодится позже: `price.net_price` — себестоимость, которую продавец может передать в Ozon
через `/v1/product/import/prices` (`prices.net_price`); `/v1/product/prices/details` — цена
для покупателя (`customer_price`), но только с подпиской Premium Pro.

### Категории товаров (шаг 10.3)

`/v1/description-category/tree` сверен с выгрузкой 2026-10. Дерево — весь каталог Ozon, поэтому сохраняем только категории
товаров магазинов и их родителей (`ozon_categories`: id = `description_category_id`, название, путь «Автотовары /
Подшипники»); типы (`type_id`) пропускаем — у товара хранится категория. Загружаем после синхронизации товаров, если у
товара появилась неизвестная категория или справочник старше 30 дней (`services/sync/categories.py`); ошибка справочника
не проваливает синхронизацию товаров (`stats.categories_error`). Справочник общий для всех продавцов.

### Отправления (шаг 5.2)

Продажи по кластерам и складам для планирования поставок. Сверено с выгрузкой документации 2026-10.

| Наше поле | FBO: `/v3/posting/fbo/list` | FBS / rFBS: `/v4/posting/fbs/list` |
|---|---|---|
| `posting_number`, `order_number`, `status` | так же | так же |
| `ordered_at` (день — по Москве) | `created_at` (иначе `in_process_at`) | `in_process_at` (`created_at` в примере ответа нет) |
| `warehouse_id`, `warehouse_name` | `analytics_data.warehouse_id`, `warehouse_name` — склад Ozon, откуда отгрузили | `delivery_method.warehouse_id`, `name` — склад продавца |
| `cluster_from`, `cluster_to` | `financial_data.cluster_from` / `cluster_to` — кластер склада и кластер покупателя | так же |
| товары | `products[]{sku, offer_id, quantity, price{amount}}` | так же |

- Запрос: `filter{since, to}`, `limit` 100, `cursor` (первый — пустая строка), `sort_dir` ASC,
  `with{analytics_data, financial_data}` — **без** `legal_info`. Следующая страница — пока `has_next` и есть `cursor`.
- Первый запуск — 90 дней, дальше — последние 35 (отмены и смена статуса за окно расчёта). Статусы FBO: `awaiting_packaging`,
  `awaiting_deliver`, `delivering`, `delivered`, `cancelled`; в спрос не идут `cancelled*` и `not_accepted`.
- В ответе FBS — имя, телефон, email и адрес покупателя (`customer`, `addressee`), юрлицо (`legal_info`), штрихкоды и
  `scanit`, у FBO — коды активации (`digital_codes`), у FBS — `imei`. Это не храним: `raw_json` — только разрешённые поля,
  из `analytics_data` убираем город и регион. Сборку FBS сервис не делает, поэтому `scanit` и штрихкоды не нужны.
- Кластер покупателя приходит названием (`"Екатеринбург"`), остатки (`/v1/analytics/stocks`) — названием и id; сопоставляем
  по названию без учёта регистра, «ё» и пробелов. Поставку FBO Ozon принимает в кластер (`/v1/draft/*/create` —
  обязательный `cluster_info`), поэтому рекомендации — по кластеру. Заявки на поставку и их создание по плану — шаг 10.18,
  раздел «Заявки на поставку FBO» ниже.

### Заявки на поставку FBO (шаг 10.18, сверено с выгрузкой документации 2026-10)

Чтение (синхронизация `supplies`, раз в 3 часа, `services/sync/supply_orders.py`):
- `/v3/supply-order/list` (бета → основной 13.11.2025; v1 и v2 удалены): `filter.states` (обязателен, передаём все
  статусы), `sort_by` (`ORDER_CREATION`), `sort_dir`, `limit` 1–100, `last_id` → `order_ids[]`, `last_id`. Идём по `last_id`,
  пока он меняется (до 20 страниц).
- `/v3/supply-order/get`: `order_ids` (до 50) → `orders[]`: `order_id`, `order_number`, `created_date`, `state`,
  `state_updated_date`, `timeslot.timeslot.{from,to}` + `timezone_info.iana_name`, `dropoff_warehouse.name`,
  `supplies[]`: `supply_id`, `bundle_id`, `macrolocal_cluster_id`, `storage_warehouse.{warehouse_id,name}`, `state`.
  Статусы: `DATA_FILLING`, `READY_TO_SUPPLY`, `ACCEPTED_AT_SUPPLY_WAREHOUSE`, `IN_TRANSIT`,
  `ACCEPTANCE_AT_STORAGE_WAREHOUSE`, `REPORTS_CONFIRMATION_AWAITING`, `REPORT_REJECTED`, `COMPLETED`,
  `REJECTED_AT_SUPPLY_WAREHOUSE`, `CANCELLED`, `OVERDUE`. `/v1/supply-order/details` не используем — в нём водитель и
  машина (персональные данные).
- `/v1/supply-order/bundle` (заменил удалённый `/v1/supply-order/items`): `bundle_ids` (одна поставка за вызов), `limit`
  1–100, `last_id` → `items[]` (`sku`, `offer_id`, `name`, `quantity`, `quant`), `has_next`, `last_id`.
- `/v1/supply-order/act/product/get` (бета, акты FBO): `supply_id` → `supply_acts[].items[]` (`declared_quantity`,
  `fact_quantity`, `approved_quantity`, `sku_info.{sku,offer_id,name}`), `skus_defects[]` (SKU с браком). Сбой — не
  ошибка синхронизации: акты подтянутся в следующий раз. Итоговые заявки старше 180 дней — без состава и актов.
- `/v1/cluster/list` `{"cluster_type": "CLUSTER_TYPE_OZON"}` → `clusters[]`: `id`, `macrolocal_cluster_id`, `name`,
  `logistic_clusters[].warehouses[]` — кластер склада хранения и id кластера для черновика (по названию).
- `/v1/supply-order/status/counter` не нужен — считаем по загруженным заявкам; таймслоты заявки и пропуск водителя
  (`/v2/supply-order/timeslot/list`, `/v1/supply-order/pass/*`), грузоместа (`/v1/cargoes/*`), отмена
  (`/v1/supply-order/cancel`) — в кабинете Ozon.

Создание заявки по плану (`services/supply_ozon.py`, правило 6 — проверка → подтверждение → журнал):
- `/v1/draft/direct/create`: `cluster_info.{items[{sku, quantity}], macrolocal_cluster_id}`, `deletion_sku_mode`
  (обязателен; `PARTIAL` — убрать только не прошедшие единицы) → `draft_id`, `errors[]`. Черновик в кабинете не виден,
  живёт 30 минут. Лимит: 2 в минуту, 50 в час, 500 в день (429).
- `/v2/draft/create/info` `{draft_id}` → `status` (`SUCCESS` / `IN_PROGRESS` / `FAILED`), `clusters[].warehouses[]`
  (`storage_warehouse`, `availability_status.{state, invalid_reason}` — значения `state` в документации не перечислены:
  склад считаем недоступным, если есть `invalid_reason` или в `state` есть `NOT`/`UNAVAILABLE`), `errors[].items_validation[]
  .rejected_items[{sku, reasons}]`.
- `/v2/draft/timeslot/info`: `date_from`, `date_to` (не дальше 28 дней), `draft_id`, `supply_type` (`DIRECT`),
  `selected_cluster_warehouses[{macrolocal_cluster_id, storage_warehouse_id}]` → `result.drop_off_warehouse_timeslots
  .days[].timeslots[{from_in_timezone, to_in_timezone}]`, `warehouse_timezone`.
- `/v2/draft/supply/create`: `draft_id`, `selected_cluster_warehouses`, `timeslot.{from_in_timezone, to_in_timezone}`,
  `supply_type` → `error_reasons[]`; `/v2/draft/supply/create/status` `{draft_id}` → `status`, `order_id`. Тот же лимит.
- Кросс-докинг и несколько кластеров (`/v1/draft/crossdock/create`, `/v1/draft/multi-cluster/create`) — не используем:
  план поставки — в один кластер.

## Изменение (только через систему безопасности, см. BUSINESS_LOGIC.md §8)

| Назначение | Метод | Эндпоинт | Важное |
|---|---|---|---|
| Цена, мин. цена, зачёркнутая | POST | `/v1/product/import/prices` | цену товара — не больше 10 раз в час; `prices.auto_action_enabled` устарел |
| Добавить или обновить товар в акции | POST | `/v1/actions/products/update` | заменяет `/v1/actions/products/activate` |
| Убрать товар из акции | POST | `/v2/actions/products/deactivate` | заменяет `/v1/actions/products/deactivate` |
| Остатки FBS | POST | `/v2/products/stocks` | 100 пар за запрос, 80 запросов/мин, пара — раз в 30 с |

Все методы Ozon — POST: повтор после таймаута может применить изменение дважды. Для
изменяющих запросов в фазе 6 решить, где нужен `OzonClient(..., retry=False)`.

### Цена товара (сверено на шаге 6.1, отправка — шаг 6.2 ✅)

- Запрос `/v1/product/import/prices`: `{"prices": [{"product_id", "price": "1448", "min_price": "800", "old_price": "0",
  "currency_code": "RUB"}]}`, до 1000 товаров (у нас — батчами по 100, BUSINESS_LOGIC §8). Передавать только
  `product_id` (с `offer_id` вместе Ozon берёт `offer_id`). `old_price` «0» — сбросить зачёркнутую цену.
- Ответ синхронный: `result[{product_id, offer_id, updated, errors[]}]` — **номера задания (`task_id`) у метода нет**,
  поэтому в `price_changes` нет `ozon_task_id` из промпта 6.1: итог по товару — `updated` и `errors`, полный ответ — в
  `audit_log`. Применяется Ozon не мгновенно (обновить цены через 1–3 минуты).
- Ограничения Ozon, которые проверяем заранее (`services/price_safety.py`): минимальная цена ниже цены
  (`price_less_than_min_auto_price`, `min_auto_price_too_big`) и не меньше 50 % цены (`min_auto_price_too_small`);
  цену товара — не больше 10 раз в час (`error limiting: acquire limit per item…`, у нас пауза час строже).
  Остальные — только от Ozon: `price_out_of_range` (порог категории), `old_price_less_than_price`,
  `action_price_enabled_min_price_missing` (мин. цена при включённом автоприменении в акции).
- Шаг 6.2 отправляет `{"prices": [{"product_id": 1001, "price": "600.00", "currency_code": "RUB"}]}` — без `min_price` и
  `old_price` (не передано — Ozon их не меняет). Текущую цену перед предпросмотром и применением берёт тем же
  `/v5/product/info/prices` (фильтр по одному `product_id`). Установка цены идемпотентна — обычные повторы клиента при
  сбое сети не вредят, `retry=False` не нужен. Ошибки по товару — `errors[{code, message}]`; известные коды показываем
  понятным текстом (`price_updater.OZON_ERRORS`), остальные — как есть.

### Вход и выход из акции (шаг 4.3)

| Что | Запрос | Ответ |
|---|---|---|
| войти или сменить цену / количество | `/v1/actions/products/update`: `{"action_id", "products": [{"product_id", "action_price": {"amount": "430.00", "currency": "RUB"}, "stock": "3"}]}` | `active_product_ids`, `deactivated_product_ids`, `rejected` и `warnings` — `[{product_id, reason}]` |
| выйти | `/v2/actions/products/deactivate`: `{"action_id", "product_ids": ["1001"]}` | `product_ids` — кто убран |

- В акциях «весь остаток» («Эластичный бустинг», «Скидка на сток», BUSINESS_LOGIC §9) `stock` не передаём.
- Из «Эластичного» и «Максимального бустинга» Ozon выводит не через `deactivate`, а через `update` с ценой в
  пределах лимита акции — у нас такой выход не делается, пользователь идёт в кабинет Ozon.
- Оба метода идемпотентны (поставить цену, убрать товар): повтор после таймаута не вредит; таймаут сервис показывает
  как «могло примениться — обновите акции». Описания полей `rejected` / `warnings` в выгрузке свёрнуты — формат взят
  из примера `/v2/actions/auto-add/products/update`; `reason` показываем пользователю как есть.
- У кандидата (`/v2/actions/candidates`) храним `min_stock` и `recommended_stock`: количество при входе — не меньше
  `min_stock`, по умолчанию `recommended_stock`.

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
- График выплат продавца (стандартный / гибкий, комиссия) — метода нет. Определяем по фактическим
  выплатам из `/v1/finance/balance` по дням, пользователь может поправить (docs/MONEY.md §2).
- Рекламные расходы детально — это отдельный **Ozon Performance API**
  (свои ключи, OAuth client_credentials). В Seller API реклама видна только
  агрегатом в начислениях.

## Понадобится для карты сервиса (фаза 10) — сверить в своём шаге

Методы найдены в выгрузке `docs/ozon/seller_api_2026-10.txt` (22.10.2026) по названиям; поля, лимиты и права ключа
сверяет шаг, который их использует (правило 10), и переносит сюда в разделы выше.

| Для чего | Методы | Шаг |
|---|---|---|
| ~~Категории товаров (названия)~~ | `/v1/description-category/tree` — сверено, раздел «Категории товаров» выше | 10.3 ✅ |
| ~~Отчёт о реализации через API~~ | `/v2/finance/realization` — сверено, таблица выше; `/v1/finance/realization` удалён, `/posting` и `/by-day` — не используем | 10.12 ✅ |
| ~~Взаиморасчёты, компенсации~~ | `/v1/finance/mutual-settlement`, `/compensation`, `/decompensation` + `/v1/report/info` — сверено, выше; `/v1/finance/cash-flow-statement/list` — уже в 3.6 | 10.12 ✅ |
| Движение денег по статьям (ДДС, бюджеты) | `/v1/finance/cash-flow-statement/list` (детали) | 10.23 |
| Отчёты, которые Ozon формирует по запросу | `/v1/report/*/create`, `/v1/report/list` (товары, отправления, возвраты, остатки) — те же данные сервис берёт синхронизацией; `/v1/report/info` — сверено (10.12) | по необходимости |
| Возвраты | `/v1/returns/list` | 10.8 |
| ~~Заявки на поставку FBO~~ | сверено: `/v3/supply-order/list`, `/v3/supply-order/get`, `/v1/supply-order/bundle`, акты `/v1/supply-order/act/product/get` — раздел «Заявки на поставку FBO» выше (v1/v2 списка и `/items` удалены) | 10.18 ✅ |
| ~~Черновики поставок и таймслоты (изменение в Ozon — правило 6)~~ | сверено: `/v1/draft/direct/create`, `/v2/draft/create/info`, `/v2/draft/timeslot/info`, `/v2/draft/supply/create`, `/v2/draft/supply/create/status` — там же | 10.18 ✅ |
| ~~Вывозы со складов (только списки)~~ | `/v1/removal/from-stock/list` — сверено (выгрузка 2026-10, бета): `date_from`, `date_to` (YYYY-MM-DD), `last_id`, `limit` 1–500 → `returns_summary_report_rows[]` (`offer_id`, `name`, `sku`, `quantity_for_return`, `return_created_at`, `return_state`, `box_state`, `stock_type`, `delivery_type`, `is_auto_return`, `clearing_warehouse_name`, `destination_warehouse_name` …), `last_id`; соответствует разделу кабинета «FBO → Вывоз и утилизация»; создать заявку нельзя. `/v1/removal/from-supply/list` (вывоз с поставки) — не используем | 10.17 ✅ |
| ~~Кластеры~~ | `/v1/cluster/list` — сверено (10.18): кластер склада хранения заявки и `macrolocal_cluster_id` для черновика | 10.18 ✅ |
