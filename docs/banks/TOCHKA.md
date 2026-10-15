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

**Сделано в шаге 3.12:** клиент вставляет токен (ключ), выпущенный в интернет-банке Точки для своей интеграции, —
`Authorization: Bearer <токен>`; срок берём из поля `exp`, если токен — JWT. Реализация —
`backend/app/integrations/bank/tochka.py`, методы и поля сверены со спецификацией v1.102.0; глубина истории при
подключении — год (в спецификации не указана). Не сверено (страниц нет в спецификации, сайт из облачной среды не
открывается): как именно клиент выпускает токен для своей компании, нужно ли для такого токена создавать разрешение
(consent), лимиты запросов. Интерфейс «Банки» пишет «Интеграции и API → выпустить ключ» — поправить по документации.

**Открытый вопрос (был поставлен на шаг 3.12, остаётся):** пошаговый порядок OAuth (регистрация приложения,
`client_id`/`client_secret`, адрес возврата, обмен кода на токен, срок жизни и обновление
токена) и есть ли у Точки токен, который клиент выпускает сам для своей интеграции, описаны
на страницах документации, которых нет в спецификации («Авторизация по OAuth 2.0»,
«Выписки», «Вебхуки», «Лимиты»). Их нужно будет выгрузить в `docs/banks/tochka/` так же.

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
