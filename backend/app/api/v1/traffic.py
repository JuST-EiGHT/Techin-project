"""«Продвижение → Внешний трафик» (фаза 11). Шаг 11.2: собранные ссылки сохраняются (POST /links), история — версии,
диплинк и подменный артикул из кабинета, срок диплинка, «Открыть» продлевает. Шаг 11.1: префикс для метки UTM_CAMPAIGN (настройка магазина) и конструктор
ссылок с UTM-метками — товары из каталога или список ссылок, выгрузка Excel / CSV, QR-код. API у Ozon для внешнего трафика
нет: ссылки сервис собирает сам по правилам Ozon (engine/traffic_links)."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine import traffic_links as tl
from app.models.user import User
from app.schemas.traffic import (
    BuildRequest,
    BuildResult,
    ChannelOut,
    LinkList,
    LinkRow,
    LinkSaved,
    LinkUpdate,
    LinkVersion,
    MacroOut,
    PrefixUpdate,
    ProductOption,
    TrafficSettings,
)
from app.services import audit
from app.services import traffic as svc
from app.services import traffic_history as history
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/traffic", tags=["traffic"])


def _settings(prefix: str | None) -> TrafficSettings:
    return TrafficSettings(
        utm_prefix=prefix, cabinet_url=tl.OZON_CABINET_URL, sections=tl.SECTIONS, max_links=tl.MAX_LINKS,
        channels=[
            ChannelOut(key=c.key, label=c.label, source=c.source, medium=c.medium, fixed_source=c.fixed_source,
                       hint=c.hint, macros=[MacroOut(code=m.code, label=m.label) for m in c.macros],
                       source_options=list(c.source_options), medium_options=list(c.medium_options))
            for c in tl.CHANNELS.values()
        ],
    )


def _bad(exc: tl.LinkError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/settings", response_model=TrafficSettings)
async def get_settings(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Префикс магазина, каналы и макросы рекламных сетей, разделы Ozon, лимит ссылок за раз."""
    return _settings(await svc.get_prefix(db, user.id))


@router.put("/settings", response_model=TrafficSettings)
async def put_settings(data: PrefixUpdate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Префикс для метки UTM_CAMPAIGN: vendor_org_<цифры> из кабинета Ozon; пусто — убрать."""
    try:
        return _settings(await svc.set_prefix(db, user.id, data.utm_prefix))
    except tl.LinkError as exc:
        raise _bad(exc) from exc


@router.get("/products", response_model=list[ProductOption])
async def products(
    search: str | None = Query(None, max_length=100, description="Артикул, название, SKU или штрихкод"),
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    return await svc.search_products(db, user.id, search, limit)


@router.post("/links/build", response_model=BuildResult, dependencies=[Depends(audit.read_only)])
async def build_links(data: BuildRequest, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Собрать ссылки с метками. Ссылки не сохраняются (история — шаг 11.2); у неподходящей строки — ошибка."""
    try:
        return await svc.build(db, user.id, data)
    except tl.LinkError as exc:
        raise _bad(exc) from exc


@router.post("/links/export", response_class=Response, dependencies=[Depends(audit.read_only)])
async def export_links(
    data: BuildRequest,
    format: Literal["xlsx", "csv"] = Query("xlsx"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Те же ссылки файлом (собираются заново) — только собранные, строки с ошибками не попадают."""
    try:
        result = await svc.build(db, user.id, data)
    except tl.LinkError as exc:
        raise _bad(exc) from exc
    name = f"Ссылки {result.campaign} {today_msk():%d.%m.%Y}"
    if format == "csv":
        return attachment(svc.to_csv(result), "text/csv; charset=utf-8", f"{name}.csv", "utm-links.csv")
    return attachment(svc.to_xlsx(result), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      f"{name}.xlsx", "utm-links.xlsx")


@router.get("/qr", response_class=Response)
async def qr_code(
    url: str = Query(..., max_length=4000),
    format: Literal["svg", "png"] = Query("svg"),
    download: bool = Query(False),
    user: User = Depends(get_current_store),
):
    """QR-код ссылки Ozon (только ссылки, которые можно разметить)."""
    try:
        content = svc.qr(url, format)
    except tl.LinkError as exc:
        raise _bad(exc) from exc
    media = "image/svg+xml" if format == "svg" else "image/png"
    if download:
        return attachment(content, media, f"QR-код.{format}", f"qr.{format}")
    return Response(content, media_type=media)


# ---------- история ссылок (шаг 11.2) ----------

Tab = Literal["all", "deeplink", "expiring", "substitute", "deleted"]


def _gone() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка не найдена")


@router.post("/links", response_model=BuildResult, status_code=status.HTTP_201_CREATED)
async def create_links(data: BuildRequest, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Собрать и сохранить в «Созданные ссылки»: собранные строки — новые ссылки (такая же уже есть — она же), строки с
    ошибкой не сохраняются."""
    try:
        return await history.save_build(db, user.id, data)
    except tl.LinkError as exc:
        raise _bad(exc) from exc


async def _list(db, user, tab, search, date_from, date_to, campaign, source, medium, channel,
                product_id=None) -> LinkList:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    return await history.list_links(db, user.id, tab=tab, search=search, date_from=date_from, date_to=date_to,
                                    campaign=campaign, source=source, medium=medium, channel=channel,
                                    product_id=product_id)


@router.get("/links", response_model=LinkList)
async def list_links(
    tab: Tab = Query("all", description="all · deeplink — с диплинком · expiring — диплинк истекает за 7 дней или уже "
                     "не работает · substitute — с подменным артикулом · deleted — удалённые"),
    search: str | None = Query(None, max_length=200, description="Артикул, название, SKU, ссылка, кампания"),
    date_from: date | None = Query(None, description="Создана с"),
    date_to: date | None = Query(None),
    campaign: str | None = Query(None, max_length=64, description="Название кампании (после префикса)"),
    source: str | None = Query(None, max_length=200),
    medium: str | None = Query(None, max_length=200),
    channel: str | None = Query(None, max_length=16),
    product_id: uuid.UUID | None = Query(None, description="Ссылки одного товара — для «Статистики товара» (11.6)"),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    return await _list(db, user, tab, search, date_from, date_to, campaign, source, medium, channel, product_id)


@router.get("/links/export", response_class=Response)
async def export_history(
    tab: Tab = Query("all"),
    search: str | None = Query(None, max_length=200),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    campaign: str | None = Query(None, max_length=64),
    source: str | None = Query(None, max_length=200),
    medium: str | None = Query(None, max_length=200),
    channel: str | None = Query(None, max_length=16),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Созданные ссылки в Excel — те же, что на странице (вкладка, поиск, период, фильтры)."""
    res = await _list(db, user, tab, search, date_from, date_to, campaign, source, medium, channel)
    return attachment(history.to_xlsx(res.items), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      f"Ссылки с UTM {today_msk():%d.%m.%Y}.xlsx", "utm-links-history.xlsx")


@router.patch("/links/{link_id}", response_model=LinkSaved)
async def update_link(link_id: uuid.UUID, data: LinkUpdate, user: User = Depends(get_current_store),
                      db: AsyncSession = Depends(get_db)):
    """Правка — новая версия, прежняя остаётся в истории ссылки."""
    try:
        return await history.update(db, user.id, link_id, data)
    except history.NotFound as exc:
        raise _gone() from exc
    except tl.LinkError as exc:
        raise _bad(exc) from exc


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Удалить из списка: ссылка и её версии остаются — «Удалённые», можно вернуть."""
    try:
        await history.delete(db, user.id, link_id)
    except history.NotFound as exc:
        raise _gone() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/links/{link_id}/restore", response_model=LinkRow)
async def restore_link(link_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        return await history.restore(db, user.id, link_id)
    except history.NotFound as exc:
        raise _gone() from exc


@router.post("/links/{link_id}/opened", response_model=LinkRow)
async def link_opened(link_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Ссылку с диплинком открыли из сервиса — отсчёт 30 дней начинается заново."""
    try:
        return await history.opened(db, user.id, link_id)
    except history.NotFound as exc:
        raise _gone() from exc
    except tl.LinkError as exc:
        raise _bad(exc) from exc


@router.get("/links/{link_id}/versions", response_model=list[LinkVersion])
async def link_versions(link_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        return await history.versions(db, user.id, link_id)
    except history.NotFound as exc:
        raise _gone() from exc
