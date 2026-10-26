"""Основа аналитики (шаг 10.3): показатели текущего магазина за период, категории и товары для фильтров; лучшие и
худшие товары «Обзора бизнеса» (шаг 10.7); продажи (10.8); структура расходов и её разбор (10.9); ABC-анализ (10.10);
лента «История изменений и событий» (10.11)."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.analytics import (
    Abc, Category, EventFeed, Leaders, Metrics, ProductOption, ProfitGroupDetail, ProfitLines, ProfitStructure,
    SalesEvent, SalesTable,
)
from app.core.files import attachment
from app.services import analytics as svc
from app.services import abc as abc_svc
from app.services import event_feed
from app.services import profit_analytics, sales_analytics
from app.services.stores import get_current_store

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/metrics", response_model=Metrics)
async def get_metrics(
    date_from: date,
    date_to: date,
    granularity: str = Query("day", description="day | week (пн–вс) | month"),
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Товары; можно несколько"),
    category_id: int | None = Query(None, description="Категория Ozon; 0 — «Без категории»"),
    compare: bool = Query(False, description="Добавить прошлый сопоставимый период"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Выручка, прибыль, маржинальность, заказы, продажи и возвраты, средняя цена — по дням, неделям или месяцам, итоги
    периода и (compare=1) прошлый сопоставимый период. Дни без данных — пропуски (null), а не ноль."""
    try:
        return await svc.metrics(db, user.id, date_from, date_to, granularity, product_id, category_id, compare)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/leaders", response_model=Leaders)
async def get_leaders(
    date_from: date,
    date_to: date,
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Только среди этих товаров"),
    category_id: int | None = Query(None, description="Только среди товаров категории; 0 — «Без категории»"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """«Обзор бизнеса»: лучшие и худшие товары по прибыли за период и по изменению продаж к прошлому сопоставимому
    периоду (только значимые — не случайные колебания)."""
    try:
        return await svc.leaders(db, user.id, date_from, date_to, product_id, category_id)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


def _filter_query(
    date_from: date,
    date_to: date,
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Товары; можно несколько"),
    category_id: int | None = Query(None, description="Категория Ozon; 0 — «Без категории»"),
) -> dict:
    return {"date_from": date_from, "date_to": date_to, "product_ids": product_id, "category_id": category_id}


@router.get("/sales", response_model=SalesTable)
async def get_sales(q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                    db: AsyncSession = Depends(get_db)):
    """«Аналитика продаж»: продажи по товарам — заказы, продано, возвраты и их доля, выручка, средняя цена, к прошлому
    сопоставимому периоду (с поправкой на дни с данными и пометкой заметных изменений)."""
    try:
        return await sales_analytics.table(db, user.id, **q)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/sales/events", response_model=list[SalesEvent])
async def get_sales_events(q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                           db: AsyncSession = Depends(get_db)):
    """События, которые влияли на продажи, по дням: цена, вход и выход из акции, «нет остатка», «снова в наличии»."""
    try:
        return await sales_analytics.events(db, user.id, **q)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/sales/export", response_class=Response)
async def export_sales(q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                       db: AsyncSession = Depends(get_db)):
    """Продажи по товарам в Excel — для чтения и работы (не шаблон для загрузки)."""
    try:
        data = await sales_analytics.table(db, user.id, **q)
        products = await svc._products(db, user.id, q["product_ids"], q["category_id"])
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None
    what = "все" if products is None else ", ".join(p.offer_id for p in products[:10]) + (" …" if len(products) > 10 else "")
    return attachment(
        sales_analytics.to_xlsx(data, what), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"Продажи {q['date_from']:%d.%m.%Y}–{q['date_to']:%d.%m.%Y}.xlsx", "sales.xlsx",
    )


@router.get("/profit/structure", response_model=ProfitStructure)
async def get_profit_structure(q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                               db: AsyncSession = Depends(get_db)):
    """«Аналитика прибыльности»: выручка, расходы по группам (себестоимость, комиссии, логистика, реклама, хранение,
    возвраты, прочее), прибыль до налога и маржа — и прошлый сопоставимый период. По магазину сумма групп = выручка −
    прибыль до налога P&L; по товарам — их строки «Прибыли по товарам»."""
    try:
        return await profit_analytics.structure(db, user.id, **q)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/profit/group", response_model=ProfitGroupDetail)
async def get_profit_group(key: str = Query(..., description="Группа расходов из /profit/structure"),
                           q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                           db: AsyncSession = Depends(get_db)):
    """Группа расходов → товары, на которые она пришлась, и начисления группы без товара."""
    try:
        return await profit_analytics.group_detail(db, user.id, key=key, **q)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/profit/lines", response_model=ProfitLines)
async def get_profit_lines(date_from: date, date_to: date,
                           offer_id: str = Query(..., min_length=1, max_length=100, description="Артикул товара"),
                           user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Исходные строки начислений товара за период: дата, статья, сумма, ID начисления, источник — API или отчёт."""
    try:
        return await profit_analytics.product_lines(db, user.id, date_from, date_to, offer_id)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


Criterion = Literal["revenue", "profit", "qty"]


@router.get("/abc", response_model=Abc)
async def get_abc(by: Criterion = Query("revenue", description="revenue — выручка, profit — прибыль, qty — продажи, шт."),
                  q: dict = Depends(_filter_query), user: User = Depends(get_current_store),
                  db: AsyncSession = Depends(get_db)):
    """ABC-анализ товаров за период: группы A, B, C по накопленной доле (границы — настройки), доля и накопленная доля,
    прошлый период и смена группы, убыточные, сравнение ABC по выручке и по прибыли."""
    try:
        return await abc_svc.analysis(db, user.id, criterion=by, **q)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/abc/export", response_class=Response)
async def export_abc(by: Criterion = Query("revenue"), q: dict = Depends(_filter_query),
                     user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """ABC-анализ в Excel — для чтения и работы (не шаблон для загрузки)."""
    try:
        data = await abc_svc.analysis(db, user.id, criterion=by, **q)
        products = await svc._products(db, user.id, q["product_ids"], q["category_id"])
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None
    what = "все" if products is None else ", ".join(p.offer_id for p in products[:10]) + (" …" if len(products) > 10 else "")
    return attachment(
        abc_svc.to_xlsx(data, what), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"ABC {q['date_from']:%d.%m.%Y}–{q['date_to']:%d.%m.%Y}.xlsx", "abc.xlsx",
    )


@router.get("/events", response_model=EventFeed)
async def get_events(
    date_from: date | None = None,
    date_to: date | None = None,
    kind: list[Literal["price", "promo", "stock", "anomaly", "action", "error"]] = Query(
        default_factory=list, description="Виды событий; не заданы — все"),
    product_id: list[uuid.UUID] = Query(default_factory=list, description="Только события этих товаров"),
    category_id: int | None = Query(None, description="Только события товаров категории; 0 — «Без категории»"),
    before: str | None = Query(None, max_length=200, description="Курсор: next с прошлой страницы"),
    limit: int = Query(event_feed.DEFAULT_LIMIT, ge=1, le=event_feed.MAX_LIMIT),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """«История изменений и событий»: цены (через сервис и нет), акции, остатки и поставки, аномалии, действия
    пользователя, ошибки загрузки — по времени назад, страницами по курсору."""
    try:
        return await event_feed.feed(db, user.id, date_from=date_from, date_to=date_to, kinds=kind,
                                     product_ids=product_id, category_id=category_id, before=before, limit=limit)
    except svc.AnalyticsError as exc:
        raise HTTPException(exc.code, exc.message) from None


@router.get("/categories", response_model=list[Category])
async def get_categories(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Категории товаров магазина (не архивных) с числом товаров."""
    return await svc.categories(db, user.id)


@router.get("/products", response_model=list[ProductOption])
async def find_products(
    search: str | None = Query(None, max_length=100, description="Часть артикула, названия или SKU"),
    id: list[uuid.UUID] = Query(default_factory=list, description="Конкретные товары — подписать выбранные в фильтре"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Товары для фильтра «товар» — до 20 подходящих."""
    return await svc.search_products(db, user.id, search, ids=id or None)
