"""Баланс у Ozon по дням (шаг 3.6): `POST /v1/finance/balance` (бета, «Финансы → Баланс»), период ≤ 30 дней.

Дат выплат в ответе нет, поэтому запрос — на один день: выплаты за день = выплата этого дня (предположение,
проверить на реальном ключе — docs/OZON_API.md). Дни — как у начислений: без периода — вчера, сегодня и
незагруженные за 90 дней; с периодом — он заново. Снимок пишется, только если цифры дня изменились.
Сверка: сумма дневных выплат за закончившийся полумесяц = выплаты `/v1/finance/cash-flow-statement/list`;
расхождение — в статистику запуска (предупреждение на странице), сбой сверки синхронизацию не роняет.
"""
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.integrations.ozon.errors import OzonAPIError
from app.models.ozon_balance import OzonBalanceDay
from app.services.sync.finance import BACKFILL_DAYS, OZON_MIN_DATE, today_msk

log = logging.getLogger(__name__)
FIELDS = ("opening", "accrued", "payments", "closing", "sales", "returns", "services")


@dataclass
class BalanceSyncStats:
    days: int = 0
    changed: int = 0  # дней с новым снимком
    payouts: int = 0  # дней с выплатой среди загруженных
    date_from: str | None = None
    date_to: str | None = None
    checked_halves: int = 0  # проверено полумесяцев
    mismatches: list[dict] = field(default_factory=list)  # {period, ozon, ours}
    reconcile_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money(obj: Any) -> Decimal | None:
    """{"currency_code": "RUB", "value": 123.4} или число → Decimal; нет — None."""
    if isinstance(obj, dict):
        obj = obj.get("value")
    if obj is None or obj == "":
        return None
    try:
        return Decimal(str(obj)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 — нечитаемое значение Ozon: нет данных, а не 0
        return None


def parse_balance(resp: dict) -> dict[str, Decimal | None]:
    total = resp.get("total") or {}
    flows = resp.get("cashflows") or {}
    payments = [_money(p) for p in total.get("payments") or []]
    services = [_money(s.get("amount")) for s in flows.get("services") or [] if isinstance(s, dict)]
    return {
        "opening": _money(total.get("opening_balance")),
        "accrued": _money(total.get("accrued")),
        "payments": sum((p for p in payments if p is not None), Decimal("0.00")),
        "closing": _money(total.get("closing_balance")),
        "sales": _money((flows.get("sales") or {}).get("amount")),
        "returns": _money((flows.get("returns") or {}).get("amount")),
        "services": sum((s for s in services if s is not None), Decimal("0.00")) if services else None,
    }


async def latest_day(db: AsyncSession, user_id: uuid.UUID, day: date) -> OzonBalanceDay | None:
    res = await db.execute(
        select(OzonBalanceDay).where(OzonBalanceDay.user_id == user_id, OzonBalanceDay.day == day)
        .order_by(OzonBalanceDay.synced_at.desc()).limit(1)
    )
    return res.scalar_one_or_none()


async def loaded_days(db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date) -> set[date]:
    res = await db.execute(
        select(OzonBalanceDay.day).where(
            OzonBalanceDay.user_id == user_id, OzonBalanceDay.day >= date_from, OzonBalanceDay.day <= date_to
        ).distinct()
    )
    return set(res.scalars())


async def days_to_sync(db: AsyncSession, user_id: uuid.UUID, date_from: date | None, date_to: date | None) -> list[date]:
    if date_from and date_to:
        return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    today = today_msk()
    start = max(today - timedelta(days=BACKFILL_DAYS - 1), OZON_MIN_DATE)
    have = await loaded_days(db, user_id, start, today)
    days = {today - timedelta(days=1), today}
    days.update(start + timedelta(days=i) for i in range((today - start).days + 1) if start + timedelta(days=i) not in have)
    return sorted(days)


def _halves(days: list[date], today: date) -> list[tuple[date, date]]:
    """Закончившиеся полумесяцы (1–15, 16–конец), которых касаются загруженные дни."""
    out: set[tuple[date, date]] = set()
    for d in days:
        if d.day <= 15:
            half = (d.replace(day=1), d.replace(day=15))
        else:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            half = (d.replace(day=16), nxt - timedelta(days=1))
        if half[1] < today:
            out.add(half)
    return sorted(out)


async def _reconcile(db: AsyncSession, user_id: uuid.UUID, client: OzonClient, days: list[date], stats: BalanceSyncStats) -> None:
    for start, end in _halves(days, today_msk()):
        have = await loaded_days(db, user_id, start, end)
        if len(have) < (end - start).days + 1:
            continue  # полумесяц загружен не целиком — сравнивать не с чем
        resp = await client.request("POST", "/v1/finance/cash-flow-statement/list", {
            "date": {"from": f"{start.isoformat()}T00:00:00.000Z", "to": f"{end.isoformat()}T23:59:59.000Z"},
            "with_details": True, "page": 1, "page_size": 1,
        })
        payments = ((resp.get("result") or {}).get("details") or {}).get("payments")
        if payments is None:
            continue  # Ozon не отдал детали — сверять не с чем
        ozon = sum((abs(_money(p.get("payment")) or Decimal(0)) for p in payments), Decimal("0.00"))
        ours = Decimal("0.00")
        for d in sorted(have):
            row = await latest_day(db, user_id, d)
            ours += abs(row.payments) if row else 0
        stats.checked_halves += 1
        if abs(ozon - ours) > 1:
            stats.mismatches.append({"period": f"{start:%d.%m.%Y}–{end:%d.%m.%Y}", "ozon": str(ozon), "ours": str(ours)})


async def sync_balance(
    db: AsyncSession, user_id: uuid.UUID, client: OzonClient, date_from: date | None = None, date_to: date | None = None
) -> BalanceSyncStats:
    stats = BalanceSyncStats()
    days = await days_to_sync(db, user_id, date_from, date_to)
    stats.date_from, stats.date_to = days[0].isoformat(), days[-1].isoformat()
    log.info("Баланс Ozon: пользователь %s, дней к загрузке %d", user_id, len(days))
    for day in days:
        resp = await client.request("POST", "/v1/finance/balance", {"date_from": day.isoformat(), "date_to": day.isoformat()})
        values = parse_balance(resp)
        prev = await latest_day(db, user_id, day)
        if prev is None or any(getattr(prev, f) != values[f] for f in FIELDS):
            db.add(OzonBalanceDay(user_id=user_id, day=day, raw_json=resp, synced_at=datetime.now(timezone.utc), **values))
            stats.changed += 1
        await db.commit()
        stats.days += 1
        stats.payouts += values["payments"] != 0
    try:
        await _reconcile(db, user_id, client, days, stats)
    except OzonAPIError as exc:
        stats.reconcile_error = str(exc)
    log.info("Баланс Ozon: пользователь %s → %s", user_id, stats.as_dict())
    return stats
