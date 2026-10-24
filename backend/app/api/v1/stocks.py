"""Склады и остатки (данные синхронизации из Ozon): список и сводка складов, товары склада, CSV, «Заканчиваются».
10.17: главная таблица остатков (по товарам, складам, кластерам) и её Excel; «На вывоз» — решения и вывозы Ozon."""
import uuid
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine.withdrawal import Params
from app.models.user import User
from app.models.warehouse import Warehouse
from app.schemas.stock import (
    RunningOut,
    StockResponse,
    StockTable,
    WarehouseResponse,
    WarehousesSummary,
    Removals,
    WithdrawalDecisionIn,
    WithdrawalDecisionOut,
    WithdrawalParams,
    WithdrawalReport,
)
from app.schemas.sync import utc
from app.services import stock_table
from app.services import stocks as svc
from app.services import withdrawal
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

warehouses_router = APIRouter(prefix="/warehouses", tags=["stocks"])
stocks_router = APIRouter(prefix="/stocks", tags=["stocks"])

StatusFilter = Literal["critical", "low", "normal", "excess", "no_data"]
_EMPTY_HELP = "Показывать нулевые остатки (товар ушёл со склада)"


@warehouses_router.get("", response_model=list[WarehouseResponse])
async def list_warehouses(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Warehouse).where(Warehouse.user_id == user.id).order_by(Warehouse.is_active.desc(), Warehouse.type, Warehouse.name)
    )
    totals: dict[uuid.UUID, list[int]] = defaultdict(lambda: [0, 0, 0])
    for stock, _, warehouse in await svc.latest_stocks(db, user.id):
        t = totals[warehouse.id]
        t[0] += stock.present > 0
        t[1] += stock.present
        t[2] += stock.in_transit or 0
    return [
        WarehouseResponse(
            id=w.id, ozon_warehouse_id=w.ozon_warehouse_id, name=w.name, type=w.type, cluster_name=w.cluster_name,
            is_active=w.is_active, synced_at=utc(w.synced_at),
            products_in_stock=totals[w.id][0], total_present=totals[w.id][1], total_in_transit=totals[w.id][2],
        )
        for w in res.scalars()
    ]


@warehouses_router.get("/summary", response_model=WarehousesSummary)
async def warehouses_summary(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """По каждому складу: тип, кластер, товаров с остатком, штук, в пути, товары по статусу остатка, 5 товаров с самым
    большим остатком. Общие итоги по товарам и «Требуют внимания»: заканчиваются (критично и мало) и лежат без
    движения (избыток). Статус — по товару в целом и порогам пользователя."""
    return await svc.summary(db, user.id)


class _ParamsQuery:
    """Параметры отчёта из запроса; не переданные — сохранённые у пользователя."""

    def __init__(
        self,
        total: int | None = Query(
            None, ge=0, le=withdrawal.MAX_TOTAL, description="Вывезти всего, шт.; 0 — без ограничения"
        ),
        keep_idle: int | None = Query(None, ge=0, le=10_000, description="Оставлять в кластере у товара без продаж, шт."),
        keep_selling: int | None = Query(None, ge=0, le=10_000, description="Оставлять в кластере у продающегося, шт."),
        excluded: str | None = Query(
            None, max_length=4000, description="Не трогать кластеры: id через запятую; пусто — ни один"
        ),
    ):
        self.total, self.keep_idle, self.keep_selling = total, keep_idle, keep_selling
        self.excluded = None if excluded is None else [k.strip() for k in excluded.split(",") if k.strip()]

    async def params(self, db: AsyncSession, user: User) -> Params:
        base = await withdrawal.load_params(db, user.id)
        return withdrawal.with_overrides(
            base, total=self.total, keep_idle=self.keep_idle, keep_selling=self.keep_selling, excluded=self.excluded
        )


def _view(
    warehouse_id: uuid.UUID | None = Query(None, description="Показать только этот склад"),
    priority: Literal["high", "medium", "normal"] | None = Query(None, description="Приоритет"),
    decision: Literal["undecided", "remove", "keep"] | None = Query(None, description="Решение пользователя"),
) -> withdrawal.View:
    return withdrawal.View(warehouse_id=warehouse_id, priority=priority, decision=decision)


@warehouses_router.get("/withdrawal", response_model=WithdrawalReport)
async def withdrawal_report(
    q: _ParamsQuery = Depends(), view: withdrawal.View = Depends(_view), user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Кандидаты на вывоз со складов Ozon (BUSINESS_LOGIC §6): товар, кластер, склад, сколько вывезти, причина и
    приоритет, замороженные деньги, решение пользователя. Параметры — из запроса, не переданные — сохранённые у
    пользователя; склад, приоритет и решение — только фильтр показа."""
    return await withdrawal.report(db, user.id, await q.params(db, user), view)


@warehouses_router.get("/withdrawal/export", response_class=Response)
async def withdrawal_export(
    q: _ParamsQuery = Depends(), view: withdrawal.View = Depends(_view), user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Тот же отчёт в Excel для логиста: артикул, кластер, склад, сколько вывезти; на втором листе — параметры."""
    data = await withdrawal.report(db, user.id, await q.params(db, user), view)
    return attachment(
        withdrawal.to_xlsx(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"Вывоз со складов {today_msk():%d.%m.%Y}.xlsx", "withdrawal.xlsx",
    )


@warehouses_router.post("/withdrawal/decisions", response_model=WithdrawalDecisionOut | None)
async def withdrawal_decision(
    data: WithdrawalDecisionIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """«Вывозим» / «оставить» по товару — новая запись с датой, действует последняя; undecided — отменить решение."""
    try:
        d = await withdrawal.decide(db, user.id, data.product_id, data.decision, data.qty)
    except withdrawal.WithdrawalError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    return withdrawal.decision_out(d) if d.decision != "undecided" else None


@warehouses_router.get("/withdrawal/removals", response_model=Removals)
async def withdrawal_removals(
    days: int = Query(30, description="За сколько дней: 30 или 90"),
    user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db),
):
    """Вывозы, уже оформленные в кабинете Ozon (FBO → Вывоз и утилизация): `/v1/removal/from-stock/list`, бета, только
    чтение — создать заявку через API нельзя."""
    if days not in withdrawal.REMOVALS_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период — 30 или 90 дней")
    try:
        return await withdrawal.removals(db, user.id, days)
    except withdrawal.WithdrawalError as exc:
        raise HTTPException(exc.status_code, exc.message) from None


@warehouses_router.get("/withdrawal/params", response_model=WithdrawalParams)
async def get_withdrawal_params(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return withdrawal.params_out(await withdrawal.load_params(db, user.id))


@warehouses_router.put("/withdrawal/params", response_model=WithdrawalParams)
async def put_withdrawal_params(
    data: WithdrawalParams, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Сохранить параметры отчёта у пользователя: отчёт и выгрузка без параметров в запросе возьмут их."""
    params = Params(
        total=data.total or None, keep_idle=data.keep_idle, keep_selling=data.keep_selling,
        excluded=frozenset(k.strip() for k in data.excluded if k.strip()),
    )
    return withdrawal.params_out(await withdrawal.save_params(db, user.id, params))


@warehouses_router.get("/{warehouse_id}/products", response_model=list[StockResponse])
async def warehouse_products(
    warehouse_id: uuid.UUID,
    status_: StatusFilter | None = Query(None, alias="status"),
    include_empty: bool = Query(False, description=_EMPTY_HELP),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Товары склада: остаток здесь, продажи и дни запаса товара в целом, статус, последняя продажа."""
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Склад не найден")
    rows = await svc.stock_rows(db, user.id, warehouse_id=warehouse_id, status=status_, include_empty=include_empty)
    return [StockResponse.from_row(r) for r in rows]


@stocks_router.get("", response_model=list[StockResponse])
async def list_stocks(
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: StatusFilter | None = None,
    include_empty: bool = Query(False, description=_EMPTY_HELP),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.stock_rows(
        db, user.id, warehouse_id=warehouse_id, product_id=product_id, status=status, include_empty=include_empty
    )
    return [StockResponse.from_row(r) for r in rows]


TableStatus = Literal["critical", "low", "normal", "excess", "no_data", "no_sales"]


class _TableQuery:
    def __init__(
        self,
        group: Literal["product", "warehouse", "cluster"] = Query("product", description="Строка — товар, товар × склад или товар × кластер"),
        warehouse_id: uuid.UUID | None = Query(None),
        status: TableStatus | None = Query(None, description="Статус остатка (5.1) или no_sales — за 30 дней продаж нет"),
        search: str | None = Query(None, max_length=100, description="Артикул, название или SKU"),
        sort: Literal[stock_table.SORTS] = Query("offer_id"),  # type: ignore[valid-type]
        order: Literal["asc", "desc"] = Query("asc"),
        include_empty: bool = Query(False, description=_EMPTY_HELP),
    ):
        self.kw = dict(group=group, warehouse_id=warehouse_id, status=status, search=(search or "").strip() or None,
                       sort=sort, order=order, include_empty=include_empty)


@stocks_router.get("/table", response_model=StockTable)
async def stock_table_page(q: _TableQuery = Depends(), user: User = Depends(get_current_store),
                           db: AsyncSession = Depends(get_db)):
    """Главная таблица остатков: доступно, резерв, в пути, продажи в день, дни запаса, дата окончания, стоимость
    запаса по себестоимости (нет себестоимости — null), статус; итоги."""
    return await stock_table.table(db, user.id, **q.kw)


@stocks_router.get("/table/export", response_class=Response)
async def stock_table_export(q: _TableQuery = Depends(), user: User = Depends(get_current_store),
                             db: AsyncSession = Depends(get_db)):
    """Та же таблица в Excel."""
    data = await stock_table.table(db, user.id, **q.kw)
    return attachment(
        stock_table.to_xlsx(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"Остатки {today_msk():%d.%m.%Y}.xlsx", "stocks.xlsx",
    )


@stocks_router.get("/export", response_class=Response)
async def export_stocks(
    warehouse_id: uuid.UUID | None = None,
    status: StatusFilter | None = None,
    include_empty: bool = Query(False, description=_EMPTY_HELP),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Те же строки, что в таблице остатков (склад, статус), — CSV для Excel."""
    rows = await svc.stock_rows(db, user.id, warehouse_id=warehouse_id, status=status, include_empty=include_empty)
    name = f"Остатки {today_msk():%d.%m.%Y}.csv"
    if warehouse_id and rows:
        name = f"Остатки {rows[0].warehouse.name} {today_msk():%d.%m.%Y}.csv"
    return attachment(svc.to_csv(rows), "text/csv; charset=utf-8", name, "stocks.csv")


@stocks_router.get("/running-out", response_model=RunningOut)
async def stocks_running_out(
    limit: int = Query(5, ge=1, le=50),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Заканчиваются (виджет главной): товары со статусом «Критично» и «Мало», меньше всего дней запаса первыми."""
    return await svc.running_out_widget(db, user.id, limit)
