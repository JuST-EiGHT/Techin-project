# Точка — API выписок (выжимка для шага 3.12)

Источник — официальная спецификация OpenAPI `Tochka.API` v1.102.0-stable, которую прислал
владелец: `docs/banks/tochka/swagger_v1.102.0.json` (свежая версия — по ссылке
`https://enter.tochka.com/doc/openapi/swagger.json`). Подключая API, клиент соглашается с
офертой банка (`https://tochka.com/links/offer-open-api`). Перед реализацией сверить эту
выжимку со спецификацией (правило 10 — как для Ozon).

## Адреса

| Среда | URL |
|---|---|
| Рабочая | `https://enter.tochka.com/uapi/` |
| Песочница | `https://enter.tochka.com/sandbox/v2/` |
| Токены (OAuth 2.0) | `https://enter.tochka.com/connect/token` |

## Доступ и разрешения

Авторизация — OAuth 2.0 и «разрешения» (consents): приложение создаёт список нужных прав
(`POST /consent/v1.0/consents` → `consentId`), клиент подтверждает его в банке, после этого
запросы идут с токеном. Статусы разрешения: `AwaitingAuthorisation`, `Authorised`, `Rejected`,
`Revoked`; у разрешения есть срок (`expirationDateTime`, `isValid`).

Запрашиваем **только чтение** (docs/MONEY.md — платежи из сервиса не отправляем):

| Разрешение | Зачем |
|---|---|
| `ReadAccountsBasic`, `ReadAccountsDetail` | список счетов и их `accountId` |
| `ReadBalances` | остатки |
| `ReadStatements` | выписки |
| `ReadCustomerData` | `customerCode` организации (если понадобится) |

Не запрашиваем: `CreatePaymentForSign`, `CreatePaymentOrder` (платежи), `*SBP*`, `*Acquiring*`,
`ManageInvoiceData`. `ManageWebhookData` — только если подключим вебхуки.

**Сделано в шаге 3.12 (сверено 15.10.2026):** авторизация для своей компании — **JWT-ключ** (страница «Авторизация по
JWT-токену», `docs/banks/tochka/jwt.txt`): интернет-банк → «Сервисы» → «Интеграции и API» → «Создать JWT-ключ» → название и
срок доступа → разрешения (по умолчанию выбраны все — оставляем только чтение счетов, балансов и выписок) → код из смс.
Передаётся как `Authorization: Bearer <jwt>`; разрешение (consent) создавать не нужно — оно задаётся при выпуске ключа.
Refresh-токена нет: к концу срока ключ перевыпускают в интернет-банке (обновляются ключ и `client_id`) — новый ключ
вставляют в «Банках», подключение обновится. OAuth 2.0 — для интеграций от имени других клиентов Точки (сервису станет
нужен при партнёрстве, шаг 8.5). Срок берём из поля `exp` JWT. Песочница (`docs/banks/tochka/sandbox.txt`):
`https://enter.tochka.com/sandbox/v2`, токен `sandbox.jwt.token`, ответы фиксированные — реализация
(`backend/app/integrations/bank/tochka.py`) на ней проверена. Выписка (`statements.txt`): асинхронно, только финальные
операции, заказанная выписка хранится 24 часа. Спецификация на 15.10.2026 — та же v1.102.0.

**TLS.** `enter.tochka.com` подписан корневым сертификатом Минцифры, как и Т-Банк — см. `TBANK.md`, «TLS».

**Не нашли в документации:** лимиты запросов к выпискам (держимся 10 запросов/с) и глубину истории (при подключении
берём год).

## Методы

| Метод | Что | Разрешение |
|---|---|---|
| `GET /open-banking/v1.0/accounts` | счета организации: `accountId`, `customerCode`, статус, валюта, тип, `accountDetails` | ReadAccounts* |
| `GET /open-banking/v1.0/balances` | остатки по всем счетам сразу | ReadBalances |
| `GET /open-banking/v1.0/accounts/{accountId}/balances` | остатки по одному счёту | ReadBalances |
| `POST /open-banking/v1.0/statements` | заказать выписку за период | ReadStatements |
| `GET /open-banking/v1.0/accounts/{accountId}/statements/{statementId}` | готовая выписка или её статус | ReadStatements |
| `GET /open-banking/v1.0/statements` | заказанные выписки со статусами (`limit`) | ReadStatements |
| `PUT/POST/GET/DELETE /webhook/v1.0/{client_id}` | вебхуки приложения | — |

### Выписка — асинхронно

1. `POST /open-banking/v1.0/statements` с телом
   `{"Data": {"Statement": {"accountId": "…", "startDateTime": "…", "endDateTime": "…"}}}`
   (даты ISO 8601) → `statementId`, статус `Created`.
2. Опрашивать `GET /open-banking/v1.0/accounts/{accountId}/statements/{statementId}` до статуса
   `Ready` (бывают `Processing`, `Error`). В выписку попадают **только операции в финальном
   статусе**.
3. Ответ: `startDateBalance`, `endDateBalance` и список `Transaction`.

### Операция (`TransactionModel`) → наша `bank_transactions`

| Поле Точки | Наше |
|---|---|
| `transactionId` (+ `paymentId`) | банковский id операции — ключ против дублей |
| `creditDebitIndicator`: `Credit` / `Debit` | приход (+) / расход (−) |
| `status`: `Booked` / `Pending` | только `Booked` — окончательные |
| `Amount.amount`, `Amount.currency`, `Amount.amountNat` (в рублях по курсу ЦБ) | сумма, валюта |
| `documentProcessDate` | дата операции |
| `documentNumber`, `transactionTypeCode` (платёжное поручение, мемориальный ордер …) | номер и вид документа |
| `description` | назначение платежа |
| `DebtorParty` / `CreditorParty`: `inn`, `name`, `kpp` | контрагент (для прихода — плательщик, для расхода — получатель); ИНН — для правил разнесения и поступлений от маркетплейсов |
| `DebtorAccount` / `CreditorAccount`: `identification` | счёт контрагента |
| `DebtorAgent` / `CreditorAgent`: БИК, корсчёт, название банка | банк контрагента |
| `TaxFields`: КБК, ОКТМО, основание, период | распознавание налоговых платежей (правила, шаг 3.12) |

### Остатки (`BalanceModel`)

`type`: `OpeningAvailable` (начальный остаток), `ClosingAvailable` (доступный баланс),
`Expected` (заблокированные средства), `OverdraftAvailable` (доступный овердрафт);
`creditDebitIndicator`, `dateTime`, `Amount`. Для «остатка на счетах» — `ClosingAvailable`.

### Вебхуки

События (`WebhookTypeEnum`): `incomingPayment`, `outgoingPayment`, `incomingSbpPayment`,
`acquiringInternetPayment`, `incomingSbpB2BPayment`, `customWebhook`. Подписка — на
`client_id` приложения, есть тестовая отправка. Для нас полезны `incomingPayment` и
`outgoingPayment`: обновлять операции сразу, а не только по расписанию. Без публичного адреса
сервиса (до деплоя, шаг 8.4) — только опрос по расписанию.

### Ответы

У всех методов обёртка `{"Data": …, "Links": {"self"}, "Meta": {"totalPages"}}`; ошибки —
400, 403, 404, 424, 500.
