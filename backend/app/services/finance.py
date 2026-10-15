"""Чтение начислений для API: строки с фильтрами и агрегаты по дням (только source='api')."""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accrual import SOURCE_API, Accrual, AccrualType
from app.models.product import Product

ZERO = Decimal("0.00")


@dataclass
class Totals:
    income: Decimal = ZERO  # сумма положительных начислений
    expense: Decimal = ZERO  # сумма отрицательных (со знаком минус)

    @property
    def net(self) -> Decimal:
        return self.income + self.expense


def _dec(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _filters(user_id: uuid.UUID, date_from: date, date_to: date) -> list:
    return [
        Accrual.user_id == user_id, Accrual.source == SOURCE_API,
        Accrual.accrual_date >= date_from, Accrual.accrual_date <= date_to,
    ]


_income = func.sum(case((Accrual.amount > 0, Accrual.amount), else_=0))
_expense = func.sum(case((Accrual.amount < 0, Accrual.amount), else_=0))


async def list_accruals(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, *,
    type_id: int | None = None, product_id: uuid.UUID | None = None, posting_number: str | None = None,
    limit: int = 100, offset: int = 0,
) -> tuple[list[tuple[Accrual, AccrualType, Product | None]], int, Totals]:
    where = _filters(user_id, date_from, date_to)
    if type_id is not None:
        where.append(Accrual.accrual_type_id == type_id)
    if product_id is not None:
        where.append(Accrual.product_id == product_id)
    if posting_number:
        where.append(Accrual.posting_number == posting_number)

    agg = (await db.execute(select(func.count(), _income, _expense).where(*where))).one()
    rows = await db.execute(
        select(Accrual, AccrualType, Product)
        .join(AccrualType, AccrualType.id == Accrual.accrual_type_id)
        .outerjoin(Product, Product.id == Accrual.product_id)
        .where(*where)
        .order_by(Accrual.accrual_date.desc(), Accrual.posting_number, Accrual.sync_scope, Accrual.line_no)
        .limit(limit)
        .offset(offset)
    )
    return [tuple(r) for r in rows.all()], agg[0], Totals(_dec(agg[1]), _dec(agg[2]))


async def daily_totals(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date
) -> tuple[list[tuple[date, Totals, int]], Totals]:
    res = await db.execute(
        select(Accrual.accrual_date, _income, _expense, func.count())
        .where(*_filters(user_id, date_from, date_to))
        .group_by(Accrual.accrual_date)
        .order_by(Accrual.accrual_date)
    )
    days, total = [], Totals()
    for day, income, expense, lines in res.all():
        t = Totals(_dec(income), _dec(expense))
        total = Totals(total.income + t.income, total.expense + t.expense)
        days.append((day if isinstance(day, date) else date.fromisoformat(str(day)), t, lines))
    return days, total
