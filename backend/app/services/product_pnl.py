"""Прибыль по товарам за период или по отчёту (шаг 3.4). Расчёт — engine/product_pnl.

Данные — те же строки начислений по дням, что у P&L периода (services/pnl_data): источник за каждый день
auto / xlsx / api, пропуски видны. Себестоимость с НДС поставщика или без — по налоговому профилю на
начало периода. Результат периода кэшируется (pnl_cache, сброс — как у P&L); сортировка, поиск и
страницы — по нему, без пересчёта.
"""
import csv
import io
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import product_pnl as engine
from app.integrations.xlsx.common import norm
from app.models.accrual import SOURCE_XLSX
from app.models.report import Report
from app.services import pnl_data
from app.services.costs import load_cost_history
from app.services.pnl_cache import cache
from app.services.pnl_service import PnLRequest, PnLRequestError, coverage_out, coverage_warnings
from app.services.tax_profile import default_supplier_vat_pct, load_profiles, profile_on

SORTS = ("revenue", "profit", "margin", "qty", "total")
WITHOUT_VAT = "without_vat"
WITH_VAT = "with_vat"


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(Decimal(v).quantize(engine.CENT))


@dataclass
class Runner:
    """Движок прибыли по товарам с себестоимостью и НДС периода: run(строки) — для всего периода или его части
    (аналитика по дням, шаг 10.3, — тот же расчёт, что и «Прибыль по товарам»)."""

    posting_offers: dict[str, set[str]]
    cost_of: engine.CostLookup
    with_vat: bool
    default_vat: Decimal

    def __call__(self, lines: list[pnl_data.Line]) -> engine.ProductPnL:
        return engine.compute(
            [engine.LineIn(ln.day, ln.type_name, ln.amount, ln.quantity, ln.offer_id, ln.sku, ln.name, ln.posting_number)
             for ln in lines],
            self.posting_offers, self.cost_of, self.with_vat, self.default_vat,
        )


async def runner(db: AsyncSession, user_id: uuid.UUID, req: PnLRequest, lines: list[pnl_data.Line]) -> Runner:
    history = await load_cost_history(db, user_id)
    row = profile_on(await load_profiles(db, user_id), req.date_from)
    with_vat = not (row is not None and row.vat_mode == "general")  # НДС с вычетом — поставщику без НДС
    if req.vat_multiplier is not None:
        default_vat = req.vat_multiplier
    else:
        default_vat = 1 + (Decimal(row.supplier_vat_pct) if row else default_supplier_vat_pct(req.date_from)) / 100

    def cost_of(offer: str, day):
        rec = history.record_at(offer, day)
        if rec is None:
            return None
        return Decimal(rec.cost_price), Decimal(rec.vat_multiplier) if rec.vat_multiplier is not None else None

    numbers = {ln.posting_number for ln in lines if not ln.offer_id and ln.posting_number}
    return Runner(await pnl_data.posting_offers(db, user_id, numbers), cost_of, with_vat, default_vat)


async def compute(db: AsyncSession, user_id: uuid.UUID, req: PnLRequest, report: Report | None = None) -> dict:
    """Все товары периода (или отчёта по начислениям), «Не распределено», итоги, источник и предупреждения."""
    if report is not None:
        if report.report_type != "accrual":
            raise PnLRequestError(
                "В отчёте о реализации нет удержаний Ozon по товарам — прибыль по товарам считается по отчётам по "
                "начислениям и данным Ozon API. Продажи и себестоимость по товарам — на странице отчёта."
            )
        req = replace(req, date_from=report.period_start, date_to=report.period_end, source=SOURCE_XLSX)
    req.check()
    params = {**req.params(), "report_id": str(report.id) if report else None}
    if (hit := await cache.get(user_id, "by-product", params)) is not None:
        return hit

    cov = (
        pnl_data.report_coverage(report) if report is not None
        else await pnl_data.coverage(db, user_id, req.date_from, req.date_to, req.source)
    )
    lines = await pnl_data.load_lines(db, user_id, cov)
    run = await runner(db, user_id, req, lines)
    with_vat, default_vat = run.with_vat, run.default_vat
    result = run(lines)

    warnings = coverage_warnings(cov)
    missing = [r for r in result.rows if r.cost_missing]
    if missing:
        warnings.append(
            f"Не найдена себестоимость у {len(missing)} товаров — прибыль и маржа по ним не считаются. "
            "Добавьте себестоимость в справочник."
        )
    if result.revenue_lines_without_article:
        warnings.append(
            f"Строк «Выручка» без артикула: {result.revenue_lines_without_article} — они в «Не распределено»."
        )
    if result.unallocated.multi_product:
        warnings.append(
            f"Начисления по отправлениям с несколькими товарами без указания товара — {_s(result.unallocated.multi_product)} ₽ "
            "— в «Не распределено»: делить их между товарами было бы догадкой."
        )
    if any(r.manual_total is not None for r in cov.reports):
        warnings.append("Суммы — по начислениям Ozon, без ручных правок «сколько пришло» из отчётов.")

    with_cost = [r for r in result.rows if r.profit is not None]
    profit = sum((r.profit for r in with_cost), Decimal(0))
    revenue_with_cost = sum((r.revenue for r in with_cost), Decimal(0))
    totals = {
        "products": len(result.rows),
        "qty": sum(r.qty for r in result.rows),
        "revenue": _s(sum((r.revenue for r in result.rows), Decimal(0))),
        "commission": _s(sum((r.commission for r in result.rows), Decimal(0))),
        "logistics": _s(sum((r.logistics for r in result.rows), Decimal(0))),
        "other": _s(sum((r.other for r in result.rows), Decimal(0))),
        "total": _s(sum((r.total for r in result.rows), Decimal(0))),
        "cost": _s(sum((r.cost for r in with_cost), Decimal(0))),
        "profit": _s(profit),
        "margin": _s(profit / revenue_with_cost * 100) if revenue_with_cost > 0 else None,
        "missing_cost": len(missing),
    }
    value = {
        "date_from": req.date_from.isoformat(),
        "date_to": req.date_to.isoformat(),
        "source": req.source,
        "report_id": str(report.id) if report else None,
        "data_source": cov.data_source,
        "coverage": coverage_out(cov),
        "cost_basis": WITH_VAT if with_vat else WITHOUT_VAT,
        "supplier_vat": str(default_vat.quantize(Decimal("0.001"))),
        "items": [_row(r) for r in result.rows],
        "totals": totals,
        "unallocated": {
            "total": _s(result.unallocated.total),
            "lines": result.unallocated.lines,
            "multi_product": _s(result.unallocated.multi_product),
            "by_type": {k: _s(v) for k, v in sorted(result.unallocated.by_type.items(), key=lambda kv: kv[1])},
        },
        "period_total": _s(result.period_total),
        # «на что ушли деньги»: все начисления периода по статьям — сначала самые большие удержания
        "period_by_type": {k: _s(v) for k, v in sorted(_by_type(lines).items(), key=lambda kv: kv[1])},
        "warnings": warnings,
    }
    await cache.set(user_id, "by-product", params, value)
    return value


def _by_type(lines) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for ln in lines:
        out[ln.type_name] = out.get(ln.type_name, Decimal(0)) + ln.amount
    return out


def _row(r: engine.ProductRow) -> dict:
    return {
        "offer_id": r.offer_id, "name": r.name, "sku": r.sku, "qty": r.qty, "returned_qty": r.returned_qty,
        "revenue": _s(r.revenue), "avg_price": _s(r.avg_price), "commission": _s(r.commission),
        "logistics": _s(r.logistics), "other": _s(r.other), "total": _s(r.total), "by_posting": _s(r.by_posting),
        "cost": _s(r.cost), "unit_cost": _s(r.unit_cost), "cost_missing": r.cost_missing,
        "profit": _s(r.profit), "margin": _s(r.margin),
        "by_type": {k: _s(v) for k, v in sorted(r.by_type.items(), key=lambda kv: -abs(kv[1]))},
        "per_unit": engine.per_unit(r),
    }


def select_rows(items: list[dict], search: str | None, sort: str, order: str) -> list[dict]:
    """Поиск по артикулу и названию, сортировка; пустые значения (нет себестоимости) — всегда в конце."""
    if search:
        q = norm(search)
        items = [i for i in items if q in norm(i["offer_id"]) or q in norm(i["name"] or "")]
    key = (lambda i: i["qty"]) if sort == "qty" else (lambda i: Decimal(i[sort]) if i[sort] is not None else None)
    present = [i for i in items if key(i) is not None]
    empty = [i for i in items if key(i) is None]
    present.sort(key=lambda i: (key(i), i["offer_id"]), reverse=order == "desc")
    return present + sorted(empty, key=lambda i: i["offer_id"])


# быстрые фильтры «Аналитики прибыльности» (10.9): прибыль < 0, маржа ниже порога «низкой маржи» (7.1), нет себестоимости
LOSS, LOW_MARGIN, NO_COST = "loss", "low_margin", "no_cost"
FILTERS = (LOSS, LOW_MARGIN, NO_COST)


def passes(item: dict, kind: str, low_margin: Decimal) -> bool:
    if kind == LOSS:
        return item["profit"] is not None and Decimal(item["profit"]) < 0
    if kind == LOW_MARGIN:  # и в минусе тоже — маржа ниже порога
        return item["margin"] is not None and Decimal(item["margin"]) < low_margin
    return bool(item["cost_missing"])


def filter_rows(items: list[dict], kinds: list[str], low_margin: Decimal, offers: set[str] | None = None) -> list[dict]:
    """Товары фильтра (None — все) и все условия kinds сразу."""
    return [i for i in items if (offers is None or i["offer_id"] in offers) and all(passes(i, k, low_margin) for k in kinds)]


def filter_counts(items: list[dict], low_margin: Decimal, offers: set[str] | None = None) -> dict[str, int]:
    """Сколько товаров у каждого быстрого фильтра — числа у кнопок."""
    scope = filter_rows(items, [], low_margin, offers)
    return {"all": len(scope), **{k: sum(passes(i, k, low_margin) for i in scope) for k in FILTERS}}


CSV_COLUMNS = [
    ("offer_id", "Артикул"), ("name", "Название"), ("sku", "SKU"), ("qty", "Продано, шт."),
    ("revenue", "Выручка, руб."), ("avg_price", "Средняя цена, руб."), ("commission", "Комиссия, руб."),
    ("logistics", "Логистика, руб."), ("other", "Прочие начисления, руб."), ("total", "Начислено Ozon, руб."),
    ("cost", "Себестоимость, руб."), ("profit", "Прибыль до налога, руб."), ("margin", "Маржа, %"),
]


def to_csv(value: dict, rows: list[dict], cost_title: str) -> bytes:
    """CSV для Excel: UTF-8 с BOM, «;», суммы с запятой; в конце — «Не распределено» и итог."""
    def cell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, str) and v.lstrip("-").replace(".", "", 1).isdigit():
            return v.replace(".", ",")
        return str(v)

    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([cost_title if key == "cost" else title for key, title in CSV_COLUMNS])
    for r in rows:
        w.writerow([cell("нет себестоимости" if key == "cost" and r["cost_missing"] else r[key]) for key, _ in CSV_COLUMNS])
    un = value["unallocated"]
    w.writerow([cell(v) for v in ["", "Не распределено (хранение, подписки и др.)", "", "", "", "", "", "", "",
                                  un["total"], "", "", ""]])
    t = value["totals"]
    w.writerow([cell(v) for v in ["", "Итого за период", "", t["qty"], t["revenue"], "", t["commission"],
                                  t["logistics"], t["other"], value["period_total"], t["cost"], "", ""]])
    return ("﻿" + out.getvalue()).encode("utf-8")
