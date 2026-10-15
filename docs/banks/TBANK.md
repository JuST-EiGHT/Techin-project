# Т-Банк — T-API выписок (выжимка для шага 3.12)

Источник — официальная спецификация OpenAPI T-API (`https://business.tbank.ru/openapi/docs/front/openapi.yaml`,
выгружена 15.10.2026) и страницы `developer.tbank.ru`. В репозитории (`docs/banks/tbank/`): методы выписки и счетов со
всеми схемами — `t-api_statement_accounts_2026-10.json` (вся спецификация — 5,5 МБ, остальное нам не нужно), страницы
«Получить выписку по счету» (`statement.txt`), «Выпустить токен» (`self-service-auth.txt`), «Песочница» (`sandbox.txt`).
Реализация — `backend/app/integrations/bank/tbank.py`; проверена на песочнице банка 15.10.2026.

**TLS.** Сайты и API Т-Банка подписаны корневым сертификатом Минцифры («Russian Trusted Root CA») — банк прямо просит
настроить доверие к нему. В стандартных хранилищах его нет: сертификат лежит в `backend/app/data/certs/` (с Госуслуг,
SHA-256 `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`), клиент банков
доверяет обычным корням и ему; проверку не отключаем.

## Доступ

| Что | Как |
|---|---|
| Адрес | `https://business.tbank.ru/openapi`; песочница — `https://business.tbank.ru/openapi/sandbox`, токен `TBankSandboxToken` (ответы фиксированные) |
| Токен | выпускает сам клиент: Т-Бизнес → «Интеграции» → «T-API» → «Выпустить токен»; IP-адреса или «Отключить проверку адресов»; «К чему будет доступ по API» — выбрать доступы; токен показывают один раз, отредактировать нельзя — только перевыпустить; действует 90 дней с последнего использования, при постоянном использовании — бессрочно; `Authorization: Bearer <токен>` |
| Доступы | `opensme/inn/[{inn}]/kpp/[{kpp}]/bank-accounts/get` — «Информация о счетах компании»; «Счета и выписки → Информация об операциях компании» — для выписки |
| Ошибки | `{"errorCode", "errorMessage", "errorId", "errorDetails"}`; 400, 401, 403, 422, 429, 5xx |

## Методы

| Метод | Что | Лимит |
|---|---|---|
| `GET /api/v4/bank-accounts` | счета: `accountNumber`, `name`, `status` (NORM — активный; CLSC / CLSB — закрыт; ARSS / ARSF — арест …), `currency` (код ОКВ, `643`), `bankBik`, `accountType` (Current, Tax, Tender, Overnight …), `balance` (`balance` — текущий остаток, `otb` — доступный вместе с овердрафтом, `realOtb`, `authorized` …) | 6 запросов/с |
| `GET /api/v1/statement` | операции за период: `accountNumber` (обязателен), `from` (включительно) и `to` (не включительно) — ISO 8601 по UTC, `operationStatus` (All / Authorization / Transaction), `limit` до 5000 (по умолчанию 1000), `cursor`, `withBalances`, `inns`, `categories`; данные с июня 2023 | 20 запросов/с |
| `GET /api/v1/bank-statement` | прежний метод выписки — помечен устаревшим, не используем | 10 запросов/с |

Ответ выписки: `operations[]`, `nextCursor` (есть — повторить запрос с `cursor`), `balances` (только в первой порции и при
`withBalances=true`): `balanceBegin`, `balanceEnd`, `credit`, `debit`, по дням — `balances[]`.

Операция: `operationId` (uuid; у операций с 22:00 до 03:00 может смениться — проведённую банк советует опознавать по
`docDate` + `documentNumber`), `operationDate` (UTC; для проведённых — дата проведения по балансу), `operationStatus`,
`typeOfOperation` (`Credit` — поступление, `Debit` — списание), `accountAmount` (в валюте счёта), `operationAmount`,
`rubleAmount`, `payPurpose`, `description`, `documentNumber` (не уникален), `docDate`, `category`, `counterParty`
(`name`, `inn`, `kpp`, `account`, `bankBic`, `bankName`), `payer` / `receiver` (`name`, `inn`, `kpp`, `acct`, `bicRu`,
`bankName`), `tax` (`kbk`, `oktmo`, `period` …).

## Как используем

- Счета — один запрос на обновление (счета и остатки вместе); закрытые не берём; остаток — `balance.balance`.
- Выписка — `operationStatus=Transaction`, `from` / `to` — начало дня по Москве в UTC (`to` — следующий день),
  `withBalances=true`; день операции — `operationDate` в московском времени.
- Ключ против дублей — `docDate` + `documentNumber` + сумма + направление (нет даты документа или номера — `operationId`).
- Контрагент — `counterParty`, нет — `payer` при поступлении, `receiver` при списании.
- Песочница отвечает одним и тем же `nextCursor` — повтор курсора останавливает загрузку.
