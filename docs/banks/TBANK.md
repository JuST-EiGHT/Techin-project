# Т-Банк — T-API выписок (выжимка для шага 3.12)

Официальная документация — `https://developer.tbank.ru/docs/api/t-api` (из облачной среды не открывается: сайт
недоступен напрямую, сведения ниже — из поиска по официальным страницам, октябрь 2026). Реализация —
`backend/app/integrations/bank/tbank.py`.

## Что сверено (по официальным страницам через поиск)

| Что | Как |
|---|---|
| Адрес | `https://business.tbank.ru/openapi` |
| Токен | выпускает сам клиент в Т-Бизнесе: «Интеграции» → «T-API» → «Выпустить токен»; перед выпуском — IP-адреса или отключить проверку; действует 90 дней с последнего использования (при регулярных запросах — бессрочно) |
| Доступ | «Счета и выписки» → «Информация об операциях компании с ИНН … и КПП …» (scope вида `opensme/inn/[…]/kpp/[…]/bank-statements/get`) |
| Выписка | `GET /api/v1/statement` — операции за период порциями по `limit` (до 5000); не поместились — в ответе `nextCursor`, его передают в `cursor`; `withBalances=true` — остатки в ответе (только в первой порции); данные — с июня 2023; лимит — 20 запросов в секунду |
| Устаревший метод | `GET /api/v1/bank-statement` (`accountNumber`, `from`, `till`; до 50 000 операций, 10 запросов/с) — помечен устаревшим, его не используем |
| Поля | в новом методе `debit` соответствует прежнему `outcome`, `credit` — `income` |

## Открытые вопросы — сверить по выгрузке документации

Страница метода не открылась целиком, поэтому эти имена взяты по памяти о T-API и **не сверены**. Если владелец
выгрузит страницы «Получить выписку по счету» (`get-api-v-1-statement`), «Получить счета» и «Выпустить токен» в
`docs/banks/tbank/` (как `docs/ozon/`), поправить только `tbank.py` и тесты `test_bank_api.py`:

- список счетов — `GET /api/v4/bank-accounts`: `accountNumber`, `name`, `currency` (`643`), `bankBik`, `balance.otb`
  (доступный остаток);
- параметры выписки: `accountNumber`, `from` / `to` (сейчас отправляем дату и время с часовым поясом
  `2026-10-01T00:00:00+03:00`), `limit`, `cursor`, `withBalances`;
- ответ: `operations[]` — `operationId`, `operationDate`, `typeOfOperation` (`Credit` / `Debit`), `accountAmount`
  (или `operationAmount`), `payPurpose` / `description`, `documentNumber`, `payer` / `receiver` (`name`, `inn`, `acct`,
  `bicRu`); `balances.incoming` / `balances.outgoing`; `nextCursor`;
- только ли проведённые операции отдаёт `/statement` (авторизации — отдельный метод `/api/v1/account-operations`).
