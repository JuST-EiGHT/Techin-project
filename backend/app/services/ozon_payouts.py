"""Выплаты Ozon для страницы и API (шаг 3.6): баланс по дням, история выплат, график, прогноз.

График: настройка пользователя (`ozon_payout_settings`, с историей) → «автоматически» по умолчанию:
определяется по фактическим выплатам (engine/payouts.detect_schedule); выплат мало — стандартный график
Ozon из app/data/ozon_payouts.json. Не хватает лага или комиссии в своей настройке — берутся из
найденного по выплатам или стандартного графика.
"""
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payouts as engine
from app.models.ozon_balance import MODES, OzonBalanceDay, OzonPayoutSettings
from app.services.sync.finance import today_msk
from app.services.sync.runner import latest_run

STANDARD_FILE = Path(__file__).resolve().parents[1] / "data" / "ozon_payouts.json"
HISTORY_WEEKS = 12
ZERO = Decimal(0)


class PayoutSettingsError(Exception):
    """Настройка графика не подходит. Текст — для пользователя."""


@lru_cache(maxsize=1)
def _standard_rules() -> list[dict]:
    return json.loads(STANDARD_FILE.read_text(encoding="utf-8"))["standard"]


def standard_schedule(day: date) -> engine.Schedule:
    rule = [r for r in _standard_rules() if date.fromisoformat(r["from"]) <= day][-1]
    return engine.Schedule(rule["mode"], rule["weekday"], rule["lag_weeks"], Decimal(rule["fee_pct"]), "standard")


async def load_days(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> dict[date, OzonBalanceDay]:
    """Последний снимок каждого загруженного дня."""
    res = await db.execute(
        select(OzonBalanceDay).where(
            OzonBalanceDay.user_id == user_id, OzonBalanceDay.day >= date_from, OzonBalanceDay.day <= date_to
        ).order_by(OzonBalanceDay.day, OzonBalanceDay.synced_at)
    )
    return {row.day: row for row in res.scalars()}  # позже записанный перекрывает


def weekly_accrued(days: dict[date, OzonBalanceDay]) -> dict[date, Decimal]:
    """Начислено по неделям (пн → сумма) — только недели, все 7 дней которых загружены."""
    weeks: dict[date, list[Decimal | None]] = {}
    for day, row in days.items():
        weeks.setdefault(engine.monday(day), []).append(row.accrued)
    return {w: sum(v, ZERO) for w, v in weeks.items() if len(v) == 7 and all(x is not None for x in v)}


async def current_settings(db: AsyncSession, user_id: uuid.UUID) -> tuple[OzonPayoutSettings | None, list[OzonPayoutSettings]]:
    res = await db.execute(
        select(OzonPayoutSettings).where(OzonPayoutSettings.user_id == user_id)
        .order_by(OzonPayoutSettings.effective_from, OzonPayoutSettings.created_at)
    )
    history = list(res.scalars())
    today = today_msk()
    effective = [s for s in history if s.effective_from <= today]
    return (effective[-1] if effective else None), history


def resolve(row: OzonPayoutSettings | None, detected: engine.Schedule | None, today: date) -> engine.Schedule:
    """Какой график действует: свой, найденный по выплатам или стандартный."""
    standard = standard_schedule(today)
    if row is None or row.mode == "auto":
        if detected is None:
            return standard
        if detected.mode == "weekly" and detected.lag_weeks is None:
            detected.lag_weeks = standard.lag_weeks
        return detected
    if row.mode == "standard":
        return standard
    if row.mode == "daily":
        return engine.Schedule("daily", source="user", fee_pct=Decimal(row.fee_pct or 0))
    # weekly / custom: свой день недели; лаг и комиссия — свои или найденные по выплатам, иначе стандартные
    found = detected if detected is not None and detected.mode == "weekly" else None
    lag = row.lag_weeks if row.lag_weeks is not None else (found.lag_weeks if found and found.lag_weeks else standard.lag_weeks)
    fee = Decimal(row.fee_pct) if row.fee_pct is not None else (found.fee_pct if found else standard.fee_pct)
    return engine.Schedule("weekly", row.weekday, lag, fee, "user")


def _d(v: Decimal | None) -> str | None:
    return None if v is None else str(Decimal(v).quantize(Decimal("0.01")))


async def payouts(db: AsyncSession, user_id: uuid.UUID, horizon_weeks: int = 8) -> dict:
    today = today_msk()
    start = engine.monday(today) - timedelta(weeks=HISTORY_WEEKS + engine.MAX_LAG + 1)
    days = await load_days(db, user_id, start, today)
    weeks = weekly_accrued(days)
    facts = [engine.Payout(d, abs(r.payments)) for d, r in sorted(days.items()) if r.payments]
    detected = engine.detect_schedule(facts, weeks, today, HISTORY_WEEKS)
    row, _ = await current_settings(db, user_id)
    schedule = resolve(row, detected, today)

    latest = max(days.values(), key=lambda r: r.day) if days else None
    balance = latest.closing if latest is not None else None
    paid_today = today in days and days[today].payments != 0
    plan = engine.forecast(schedule, today, weeks, paid_today, balance, horizon_weeks)
    history_from = today - timedelta(weeks=HISTORY_WEEKS)
    history = []
    for p in sorted(facts, key=lambda p: p.day, reverse=True):
        if p.day < history_from:
            break
        exp = engine.expected(schedule, p.day, weeks)
        history.append({
            "date": p.day.isoformat(), "amount": _d(p.amount),
            "week_from": exp[0].isoformat() if exp else None,
            "week_to": (exp[0] + timedelta(days=6)).isoformat() if exp else None,
            "expected": _d(exp[1]) if exp else None,
            "diff": _d(p.amount - exp[1]) if exp and exp[1] is not None else None,
        })
    late = engine.overdue(schedule, today, {p.day for p in facts}, set(days))

    chart: dict[date, dict] = {}
    for p in facts:
        if p.day >= history_from:
            item = chart.setdefault(engine.monday(p.day), {"paid": ZERO, "schedule": ZERO, "estimate": ZERO})
            item["paid"] += p.amount
    for f in plan:
        item = chart.setdefault(engine.monday(f.day), {"paid": ZERO, "schedule": ZERO, "estimate": ZERO})
        if f.amount is not None:
            item[f.kind] += f.amount

    warnings = []
    run = await latest_run(db, user_id, "balance", status="success")
    for m in (run.stats or {}).get("mismatches", []) if run else []:
        warnings.append(
            f"Выплаты за {m['period']}: по дням {m['ours']} ₽, в финансовом отчёте Ozon {m['ozon']} ₽ — "
            "сверьте с кабинетом Ozon («Финансы → Баланс»)."
        )
    for d in late:
        warnings.append(f"По графику ожидалась выплата {d:%d.%m.%Y}, но Ozon её не провёл — проверьте баланс в кабинете Ozon.")
    if schedule.source == "standard" and (row is None or row.mode == "auto"):
        warnings.append(
            "Выплат в истории пока мало — показываем стандартный график Ozon. Когда выплат станет больше, "
            "график определится по ним."
        )
    if schedule.mode == "irregular":
        warnings.append("Выплаты приходят нерегулярно — даты следующих выплат не прогнозируем. Можно указать график вручную.")

    next_payout = next((f for f in plan if f.amount is not None), plan[0] if plan else None)
    return {
        "has_data": bool(days),
        "last_day": latest.day.isoformat() if latest else None,
        "balance": _d(balance),
        "paid_30d": _d(sum((p.amount for p in facts if p.day > today - timedelta(days=30)), ZERO)) if days else None,
        "schedule": {
            "mode": schedule.mode, "weekday": schedule.weekday, "lag_weeks": schedule.lag_weeks,
            "fee_pct": str(schedule.fee_pct), "source": schedule.source, "confidence": schedule.confidence,
            "payouts_used": schedule.payouts_used, "title": schedule.title,
        },
        "detected": None if detected is None else {
            "mode": detected.mode, "weekday": detected.weekday, "lag_weeks": detected.lag_weeks,
            "fee_pct": str(detected.fee_pct), "confidence": detected.confidence, "title": detected.title,
        },
        "next_payout": None if next_payout is None else _forecast_out(next_payout),
        "forecast": [_forecast_out(f) for f in plan],
        "history": history,
        "overdue": [d.isoformat() for d in late],
        "weeks": [
            {"week_from": w.isoformat(), "paid": _d(v["paid"]), "schedule": _d(v["schedule"]), "estimate": _d(v["estimate"])}
            for w, v in sorted(chart.items())
        ],
        "warnings": warnings,
    }


def _forecast_out(f: engine.Forecast) -> dict:
    return {
        "date": f.day.isoformat(), "amount": _d(f.amount), "kind": f.kind,
        "week_from": f.week_from.isoformat() if f.week_from else None,
        "week_to": f.week_to.isoformat() if f.week_to else None,
    }


async def balance_days(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> list[dict]:
    days = await load_days(db, user_id, date_from, date_to)
    out = []
    for i in range((date_to - date_from).days + 1):
        d = date_from + timedelta(days=i)
        r = days.get(d)
        out.append({"date": d.isoformat(), "loaded": r is not None, **{
            f: (_d(getattr(r, f)) if r is not None else None)
            for f in ("opening", "accrued", "payments", "closing", "sales", "returns", "services")
        }})
    return out


@dataclass
class SettingsIn:
    mode: str
    weekday: int | None = None
    lag_weeks: int | None = None
    fee_pct: Decimal | None = None


def validate(data: SettingsIn) -> SettingsIn:
    if data.mode not in MODES:
        raise PayoutSettingsError("График выплат: недопустимое значение")
    if data.mode in ("weekly", "custom") and data.weekday is None:
        raise PayoutSettingsError("Укажите день недели, в который приходят выплаты")
    if data.mode in ("auto", "standard", "daily"):
        data.weekday = None
        data.lag_weeks = None
    if data.mode in ("auto", "standard"):
        data.fee_pct = None
    if data.mode == "weekly":
        data.lag_weeks = None  # лаг определяется по выплатам — для своего лага есть «свой график»
    return data


async def save_settings(db: AsyncSession, user_id: uuid.UUID, data: SettingsIn) -> tuple[OzonPayoutSettings, bool]:
    """Новая настройка — новая запись с сегодняшнего дня; такая же, как действующая, — без записи. Коммит — на вызывающем."""
    data = validate(data)
    row, _ = await current_settings(db, user_id)
    fee = None if data.fee_pct is None else Decimal(data.fee_pct).quantize(Decimal("0.01"))
    if row is not None and (row.mode, row.weekday, row.lag_weeks, row.fee_pct) == (data.mode, data.weekday, data.lag_weeks, fee):
        return row, False
    record = OzonPayoutSettings(
        id=uuid.uuid4(), user_id=user_id, effective_from=today_msk(), mode=data.mode, weekday=data.weekday,
        lag_weeks=data.lag_weeks, fee_pct=fee, created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    return record, True
