"""Главная — дашборд Ozon (шаг 3.7): только данные Ozon, без прогноза остатка и внешних операций (это 3.10).

Цифры периода и прошлого такого же периода — из P&L (pnl_service: кэш 5 минут, сохранённые итоги закрытых
месяцев), ряды выручки — из P&L по дням (тот же кэш), баланс, следующая выплата и выплаты по неделям — из
выплат Ozon (шаг 3.6). Нет данных — null, а не 0 (правило 12).
"""
import calendar
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.models.report import Report
from app.services import ozon_payouts, pnl_service
from app.services.sync.finance import today_msk
from app.services.tax_profile import load_profiles

WEEKLY_AFTER_DAYS = 45  # период длиннее — ряды по неделям
PAYOUT_WEEKS_BACK = 8  # выплаты на главной: 8 недель назад
PAYOUT_WEEKS_AHEAD = 4  # и 4 вперёд
UPCOMING_DAYS = 14  # «Деньги и ближайшие события» (шаг 10.4): выплаты Ozon на две недели вперёд
TENTH = Decimal("0.1")


def _month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _months_back(d: date, months: int) -> date:
    """Первое число месяца на `months` раньше."""
    total = d.year * 12 + d.month - 1 - months
    return date(total // 12, total % 12 + 1, 1)


def previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    """Прошлый такой же период. Целые месяцы — столько же месяцев раньше; с 1-го по N-е число месяца — прошлый
    месяц с 1-го по то же число; иначе — столько же дней сразу перед периодом (неделя — прошлая неделя)."""
    if date_from.day == 1 and date_to == _month_end(date_to):
        months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
        start = _months_back(date_from, months)
        return start, date_from - timedelta(days=1)
    if date_from.day == 1 and (date_to.year, date_to.month) == (date_from.year, date_from.month):
        start = _months_back(date_from, 1)
        return start, start.replace(day=min(date_to.day, _month_end(start).day))
    days = (date_to - date_from).days + 1
    return date_from - timedelta(days=days), date_from - timedelta(days=1)


def _num(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def change_pct(current: Decimal | None, previous: Decimal | None) -> str | None:
    """Изменение к прошлому периоду, %. Нет одной из цифр или прошлая 0 — сравнивать не с чем."""
    if current is None or previous is None or previous == 0:
        return None
    return str(((current - previous) / abs(previous) * 100).quantize(TENTH, ROUND_HALF_UP))


def _figure(current: Decimal | None, previous: Decimal | None) -> dict:
    return {
        "value": None if current is None else str(current),
        "previous": None if previous is None else str(previous),
        "change_pct": change_pct(current, previous),
    }


def _chunks(days: list[dict], size: int) -> list[list[dict]]:
    return [days[i:i + size] for i in range(0, len(days), size)]


def _point(days: list[dict]) -> tuple[str | None, bool]:
    """Сумма выручки дней с данными; ни одного дня с данными — None. Второе — есть ли дни без данных."""
    known = [Decimal(d["revenue"]) for d in days if d["revenue"] is not None]
    return (str(sum(known, Decimal(0))) if known else None), len(known) < len(days)


def revenue_series(current: list[dict], previous: list[dict]) -> dict:
    """Выручка по дням (или по 7 дней, если период длинный) рядом с прошлым периодом — по порядку дней."""
    size = 7 if len(current) > WEEKLY_AFTER_DAYS else 1
    cur, prev = _chunks(current, size), _chunks(previous, size)
    points = []
    for i, chunk in enumerate(cur):
        revenue, partial = _point(chunk)
        prev_chunk = prev[i] if i < len(prev) else []
        prev_revenue, prev_partial = _point(prev_chunk) if prev_chunk else (None, False)
        points.append({
            "date": chunk[0]["date"], "date_to": chunk[-1]["date"],
            "prev_date": prev_chunk[0]["date"] if prev_chunk else None,
            "revenue": revenue, "prev_revenue": prev_revenue, "partial": partial or prev_partial,
        })
    return {"granularity": "week" if size == 7 else "day", "points": points}


def compare_note(cur: dict, prev: dict) -> str | None:
    if prev["data_source"] == "none":
        return "За прошлый период данных нет — сравнивать не с чем."
    if cur["coverage"]["missing_days"] or prev["coverage"]["missing_days"]:
        return "В одном из периодов есть дни без данных — сравнение приблизительное."
    return None


async def ozon_dashboard(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict:
    prev_from, prev_to = previous_period(date_from, date_to)
    req = pnl_service.PnLRequest(date_from, date_to)
    prev_req = pnl_service.PnLRequest(prev_from, prev_to)
    cur = await pnl_service.get_pnl(db, user_id, req)
    prev = await pnl_service.get_pnl(db, user_id, prev_req)
    by_day = await pnl_service.get_by_day(db, user_id, req)
    prev_by_day = await pnl_service.get_by_day(db, user_id, prev_req)

    p, pp = cur["pnl"], prev["pnl"]
    # прибыль сравниваем одинаковую: остаток после налога, если налог есть в обоих периодах, иначе до налога
    net = p is not None and p["net"] is not None and (pp is None or pp["net"] is not None)
    key = "net" if net else "profit_before_tax"
    margin = p["margin"] if p and net else None
    prev_margin = pp["margin"] if pp and net else None

    payouts = await ozon_payouts.payouts(db, user_id, horizon_weeks=PAYOUT_WEEKS_AHEAD)
    today = today_msk()
    since = (today - timedelta(days=today.weekday() + 7 * PAYOUT_WEEKS_BACK)).isoformat()
    has_reports = await db.scalar(
        select(Report.id).where(Report.user_id == user_id, Report.deleted_at.is_(None)).limit(1)
    ) is not None

    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "prev_from": prev_from.isoformat(), "prev_to": prev_to.isoformat(),
        "setup": {
            "has_key": await crud_credential.has_active(db, user_id),
            "has_reports": has_reports,
            "has_balance": payouts["has_data"],
            "tax_set": bool(await load_profiles(db, user_id)),
        },
        "data_source": cur["data_source"],
        "coverage": cur["coverage"],
        "prev_data_source": prev["data_source"],
        "missing_cost_count": len(cur["missing_cost"]),
        "figures": {
            "revenue": _figure(_num(cur["revenue_base"]), _num(prev["revenue_base"])),
            "received": _figure(_num(p["effective_total"]) if p else None, _num(pp["effective_total"]) if pp else None),
            "profit": {**_figure(_num(p[key]) if p else None, _num(pp[key]) if pp else None),
                       "kind": "net" if net else "before_tax"},
            "margin": {
                "value": margin, "previous": prev_margin,
                "change_pp": None if margin is None or prev_margin is None
                else str((Decimal(margin) - Decimal(prev_margin)).quantize(TENTH, ROUND_HALF_UP)),
            },
        },
        "compare_note": compare_note(cur, prev),
        "series": revenue_series(by_day["days"], prev_by_day["days"]),
        "payouts": {
            "has_data": payouts["has_data"],
            "balance": payouts["balance"],
            "last_day": payouts["last_day"],
            # баланс не загружался — прогноз по стандартному графику без сумм не показываем: «нет данных»
            "next_payout": payouts["next_payout"] if payouts["has_data"] else None,
            "upcoming": [
                f for f in payouts["forecast"]
                if today.isoformat() <= str(f["date"]) <= (today + timedelta(days=UPCOMING_DAYS - 1)).isoformat()
            ] if payouts["has_data"] else [],
            "schedule_title": payouts["schedule"]["title"],
            "weeks": [w for w in payouts["weeks"] if w["week_from"] >= since] if payouts["has_data"] else [],
        },
    }
