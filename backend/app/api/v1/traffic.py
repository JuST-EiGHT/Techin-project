"""«Продвижение → Внешний трафик» (фаза 11). Шаг 11.1: префикс для метки UTM_CAMPAIGN (настройка магазина) и конструктор
ссылок с UTM-метками — товары из каталога или список ссылок, выгрузка Excel / CSV, QR-код. API у Ozon для внешнего трафика
нет: ссылки сервис собирает сам по правилам Ozon (engine/traffic_links)."""
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
    MacroOut,
    PrefixUpdate,
    ProductOption,
    TrafficSettings,
)
from app.services import audit
from app.services import traffic as svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/traffic", tags=["traffic"])


def _settings(prefix: str | None) -> TrafficSettings:
    return TrafficSettings(
        utm_prefix=prefix, cabinet_url=tl.OZON_CABINET_URL, sections=tl.SECTIONS, max_links=tl.MAX_LINKS,
        channels=[
            ChannelOut(key=c.key, label=c.label, source=c.source, medium=c.medium, fixed_source=c.fixed_source,
                       hint=c.hint, macros=[MacroOut(code=m.code, label=m.label) for m in c.macros])
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
