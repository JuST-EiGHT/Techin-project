"""«Отчёт о реализации товаров» через API — `/v2/finance/realization` (месяц; не раньше августа 2023; приходит не
позднее 5-го числа следующего месяца). Тот же отчёт, что «Финансы → Документы → Отчёты о реализации» в личном кабинете.

Строка — товар: `item` (артикул, SKU, название), `delivery_commission` — продажи (amount — реализовано на сумму,
quantity — штук), `return_commission` — возвраты, у обоих — баллы и софинансирования (bonus, bank_coinvestment, stars,
pick_up_point_coinvestment). Итоги — как у XLSX-отчёта о реализации (`parsers/realization.py`):
  revenue_base — «Итого реализовано (за вычетом возвратов)» = Σ продано на сумму − Σ возвращено на сумму;
  auto_total   — revenue_base + «Всего выплат от партнёров» (баллы и софинансирования продаж минус возвратов);
  cost_sales   — себестоимость × проданные штуки (как в XLSX: по колонке «Реализовано»).
Полей с описанием итогов в ответе нет — суммы собираем сами; сверка с файлом из кабинета — в чек-листе запуска (8.4a).
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.integrations.xlsx.common import ReportParseError, limit_warnings
from app.integrations.xlsx.costs import CostProvider
from app.integrations.xlsx.parsers.base import ParsedReport, SalesLedger, money
from app.schemas.report import NormalizedReportData

REALIZATION_SUFFIX = ".realization.json"  # имя сохранённого ответа: по нему отчёт перечитывается этим разбором
PARTNER_FIELDS = ("bonus", "bank_coinvestment", "stars", "pick_up_point_coinvestment")
REALIZED_TITLE = "Итого реализовано (за вычетом возвратов)"
PAYMENTS_TITLE = "Всего выплат от партнёров"


def _num(v) -> Decimal:
    if v in (None, ""):
        return Decimal(0)
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return Decimal(0)


def _date(v) -> date | None:
    if not v:
        return None
    text = str(v)[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_realization_api(payload: dict, costs: CostProvider) -> ParsedReport:
    """Ответ `/v2/finance/realization` → данные отчёта. Строк нет — ReportParseError с понятной причиной."""
    result = payload.get("result", payload) if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ReportParseError("Ozon вернул отчёт о реализации в неожиданном виде — попробуйте получить его позже.")
    header = result.get("header") or {}
    rows = result.get("rows") or []
    start, end = _date(header.get("start_date")), _date(header.get("stop_date"))
    if not rows:
        raise ReportParseError(
            "В отчёте о реализации за этот месяц нет строк: продаж не было или Ozon ещё не сформировал отчёт — он "
            "появляется до 5-го числа следующего месяца."
        )
    ledger = SalesLedger(costs)
    returned = Decimal(0)
    partners = Decimal(0)
    for row in rows:
        item = row.get("item") or {}
        sale = row.get("delivery_commission") or {}
        back = row.get("return_commission") or {}
        ledger.add(
            article=str(item.get("offer_id") or "").strip(), sku=str(item.get("sku") or ""),
            name=str(item.get("name") or "").strip(), qty=int(_num(sale.get("quantity"))),
            revenue=_num(sale.get("amount")),
        )
        returned += _num(back.get("amount"))
        partners += sum((_num(sale.get(f)) - _num(back.get(f)) for f in PARTNER_FIELDS), Decimal(0))
    realized = ledger.revenue - returned
    by_type = {t: money(v) for t, v in ((REALIZED_TITLE, realized), (PAYMENTS_TITLE, partners)) if v}
    period = f"{start:%d.%m.%Y}–{end:%d.%m.%Y}" if start and end else str(header.get("number") or "")
    warnings = ledger.warnings("таблицы")
    return ParsedReport(NormalizedReportData(
        report_type="realization", period=period, period_start=start, period_end=end,
        auto_total=money(realized + partners), revenue_base=money(realized), cost_sales=money(ledger.cost_sales),
        by_type=by_type, by_article=ledger.by_article(), missing_cost=ledger.missing_cost(),
        warnings=limit_warnings(warnings), sheet_name="Ozon API", header_row=0, rows_count=ledger.rows,
    ))
