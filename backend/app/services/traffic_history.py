"""«Внешний трафик», шаг 11.2: история ссылок с UTM-метками. Собранные ссылки сохраняются версиями (правка — новая
строка, правило 12), у ссылки — диплинк и подменный артикул из кабинета Ozon, срок диплинка (30 дней без переходов)."""
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import traffic_links as tl
from app.models.product import Product
from app.models.traffic import TrafficLink, TrafficLinkOpen
from app.schemas.traffic import (
    BuildRequest,
    BuildResult,
    DeeplinkStatus,
    LinkCounts,
    LinkFacets,
    LinkList,
    LinkRow,
    LinkSaved,
    LinkUpdate,
    LinkVersion,
    ProductRef,
)
from app.services import traffic as builder
from app.services.xlsx_export import Col, table_xlsx

TABS = ("all", "deeplink", "expiring", "substitute", "deleted")


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)  # SQLite отдаёт без пояса


async def save_build(db: AsyncSession, store_id: uuid.UUID, data: BuildRequest) -> BuildResult:
    """Собрать и сохранить: каждая собранная строка — новая ссылка (версия 1), одним batch; строки с ошибкой не
    сохраняются. Та же страница с теми же метками уже есть — не дублируем, возвращаем её."""
    result = await builder.build(db, store_id, data)
    now = _now()
    batch = uuid.uuid4()
    existing = {r.url: r.link_id for r in (await db.scalars(select(TrafficLink).where(
        TrafficLink.user_id == store_id, TrafficLink.superseded_at.is_(None), TrafficLink.deleted_at.is_(None)))).all()}
    rows = []
    for r in result.rows:
        if not r.url:
            rows.append(r)
            continue
        if r.url in existing:
            rows.append(r.model_copy(update={"link_id": existing[r.url]}))
            continue
        link = tl.parse_link(r.url)
        row = TrafficLink(
            user_id=store_id, link_id=uuid.uuid4(), version=1, batch_id=batch,
            product_id=r.product.id if r.product else None, source_url=tl.plain_url(link), section=link.section,
            channel=data.channel, campaign=result.campaign, campaign_name=tl.campaign_name_of(result.campaign),
            utm_source=result.source, utm_medium=result.medium, utm_term=result.term, utm_content=result.content,
            url=r.url, created_at=now,
        )
        db.add(row)
        existing[r.url] = row.link_id
        rows.append(r.model_copy(update={"link_id": row.link_id}))
    await db.commit()
    return result.model_copy(update={"rows": rows})


def add_link(db: AsyncSession, store_id: uuid.UUID, link: tl.OzonLink, labels: tl.Labels, *, channel: str,
             product_id: uuid.UUID | None, now: datetime) -> TrafficLink:
    """Одна новая ссылка (версия 1) без коммита — для тех, кто создаёт ссылки сам: реферальная программа (11.7)."""
    row = TrafficLink(
        user_id=store_id, link_id=uuid.uuid4(), version=1, batch_id=uuid.uuid4(), product_id=product_id,
        source_url=tl.plain_url(link), section=link.section, channel=channel, campaign=labels.campaign,
        campaign_name=tl.campaign_name_of(labels.campaign), utm_source=labels.source, utm_medium=labels.medium,
        utm_term=labels.term, utm_content=labels.content, url=tl.tagged_url(link, labels), created_at=now,
    )
    db.add(row)
    return row


# ---------- список ----------


async def _current(db: AsyncSession, store_id: uuid.UUID) -> list[TrafficLink]:
    return list((await db.scalars(select(TrafficLink).where(
        TrafficLink.user_id == store_id, TrafficLink.superseded_at.is_(None)))).all())


async def _meta(db: AsyncSession, store_id: uuid.UUID) -> tuple[dict, dict]:
    """По каждой ссылке: (создана — первая версия, число версий) и последние «Открыть» в сервисе."""
    first = {lid: (_utc(c), n) for lid, c, n in (await db.execute(
        select(TrafficLink.link_id, func.min(TrafficLink.created_at), func.count())
        .where(TrafficLink.user_id == store_id).group_by(TrafficLink.link_id))).all()}
    opens: dict[uuid.UUID, list[datetime]] = defaultdict(list)
    for lid, at in (await db.execute(select(TrafficLinkOpen.link_id, TrafficLinkOpen.opened_at)
                                     .where(TrafficLinkOpen.user_id == store_id))).all():
        opens[lid].append(_utc(at))
    return first, opens


def _status(r: TrafficLink, opens: list[datetime], now: datetime) -> DeeplinkStatus | None:
    if not r.deeplink or r.deleted_at is not None:
        return None
    s = tl.deeplink_state(_utc(r.deeplink_since) or _utc(r.created_at), opens, now)
    return DeeplinkStatus(state=s.state, expires_on=s.expires_at.date(), days_left=s.days_left,
                          last_activity=s.last_activity)


def _row(r: TrafficLink, product: Product | None, first: dict, opens: dict, now: datetime) -> LinkRow:
    created, n = first.get(r.link_id, (_utc(r.created_at), 1))
    return LinkRow(
        link_id=r.link_id, version=r.version, versions=n, created_at=created, updated_at=_utc(r.created_at),
        product=ProductRef(id=product.id, offer_id=product.offer_id, name=product.name, sku=product.ozon_sku)
        if product else None,
        source_url=r.source_url, section=r.section, channel=r.channel, campaign=r.campaign,
        campaign_name=r.campaign_name, utm_source=r.utm_source, utm_medium=r.utm_medium, utm_term=r.utm_term,
        utm_content=r.utm_content, url=r.url, deeplink=r.deeplink,
        deeplink_status=_status(r, opens.get(r.link_id, []), now), substitute_code=r.substitute_code,
        deleted=r.deleted_at is not None,
    )


def _fold(v) -> str:
    return "" if v is None else " ".join(str(v).split()).lower().replace("ё", "е")


def _tab_ok(row: LinkRow, tab: str) -> bool:
    if tab == "deleted":
        return row.deleted
    if row.deleted:
        return False
    if tab == "deeplink":
        return row.deeplink is not None
    if tab == "expiring":
        return row.deeplink_status is not None and row.deeplink_status.state != tl.DL_OK
    if tab == "substitute":
        return row.substitute_code is not None
    return True


async def list_links(
    db: AsyncSession, store_id: uuid.UUID, *, tab: str = "all", search: str | None = None,
    date_from: date | None = None, date_to: date | None = None, campaign: str | None = None,
    source: str | None = None, medium: str | None = None, channel: str | None = None, now: datetime | None = None,
    product_id: uuid.UUID | None = None,
) -> LinkList:
    """Действующие версии ссылок, новые сверху. Счётчики вкладок и значения фильтров — по ссылкам с учётом поиска,
    периода и фильтров (без вкладки), как у быстрых вкладок Ozon."""
    now = now or _now()
    current = await _current(db, store_id)
    first, opens = await _meta(db, store_id)
    ids = {r.product_id for r in current if r.product_id}
    products = {p.id: p for p in (await db.scalars(select(Product).where(Product.id.in_(ids)))).all()} if ids else {}
    rows = [_row(r, products.get(r.product_id), first, opens, now) for r in current]
    facets = LinkFacets(
        campaigns=sorted({r.campaign_name for r in rows if not r.deleted}),
        sources=sorted({r.utm_source for r in rows if not r.deleted}),
        mediums=sorted({r.utm_medium for r in rows if not r.deleted}),
    )
    q = _fold(search)

    def fits(r: LinkRow) -> bool:
        if product_id and (r.product is None or r.product.id != product_id):
            return False
        day = r.created_at.date()
        if date_from and day < date_from or date_to and day > date_to:
            return False
        if campaign and r.campaign_name != campaign or source and r.utm_source != source:
            return False
        if medium and r.utm_medium != medium or channel and r.channel != channel:
            return False
        if q:
            hay = (r.url, r.source_url, r.campaign, r.substitute_code, r.deeplink,
                   *(() if not r.product else (r.product.offer_id, r.product.name, r.product.sku)))
            return any(q in _fold(v) for v in hay)
        return True

    found = [r for r in rows if fits(r)]
    counts = LinkCounts(**{t: sum(1 for r in found if _tab_ok(r, t)) for t in TABS})
    items = sorted((r for r in found if _tab_ok(r, tab)), key=lambda r: r.created_at, reverse=True)
    return LinkList(items=items, total=len(items), counts=counts, facets=facets)


async def get_row(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID, now: datetime | None = None) -> LinkRow:
    r = await _current_of(db, store_id, link_id)
    first, opens = await _meta(db, store_id)
    product = await db.get(Product, r.product_id) if r.product_id else None
    return _row(r, product, first, opens, now or _now())


async def _current_of(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID) -> TrafficLink:
    r = await db.scalar(select(TrafficLink).where(
        TrafficLink.user_id == store_id, TrafficLink.link_id == link_id, TrafficLink.superseded_at.is_(None)))
    if r is None:
        raise NotFound
    return r


# ---------- правка: новая версия ----------


def _next(r: TrafficLink, now: datetime, **changes) -> TrafficLink:
    fields = {c: getattr(r, c) for c in (
        "user_id", "link_id", "batch_id", "product_id", "source_url", "section", "channel", "campaign", "campaign_name",
        "utm_source", "utm_medium", "utm_term", "utm_content", "url", "deeplink", "deeplink_since", "substitute_code")}
    fields.update(changes)
    return TrafficLink(id=uuid.uuid4(), version=r.version + 1, created_at=now, **fields)


async def update(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID, data: LinkUpdate,
                 now: datetime | None = None) -> LinkSaved:
    """Правка — новая версия, прежняя остаётся (superseded_at). Метки меняются — ссылка пересобирается; диплинк из
    кабинета сделан для прежних меток — его убираем и предупреждаем. Ничего не изменилось — новой версии нет."""
    now = now or _now()
    r = await _current_of(db, store_id, link_id)
    if r.deleted_at is not None:
        raise tl.LinkError("Ссылка удалена — сначала верните её")
    sent = data.model_dump(exclude_unset=True)
    warnings: list[str] = []
    prefix = await builder.get_prefix(db, store_id)
    labels_changed = any(k in sent for k in ("campaign", "source", "medium", "term", "content"))
    if labels_changed:
        ch = tl.CHANNELS.get(r.channel) or tl.CHANNELS["other"]
        labels = tl.make_labels(
            prefix=prefix or tl.campaign_value_prefix(r.campaign), campaign=sent.get("campaign", r.campaign_name),
            channel=ch.key, source=sent.get("source", r.utm_source), medium=sent.get("medium", r.utm_medium),
            term=sent.get("term", r.utm_term), content=sent.get("content", r.utm_content))
    else:
        labels = tl.Labels(r.campaign, r.utm_source, r.utm_medium, r.utm_term, r.utm_content)
    url = tl.tagged_url(tl.parse_link(r.source_url), labels)
    deeplink, since = r.deeplink, r.deeplink_since
    if "deeplink" in sent:
        new = tl.check_deeplink(sent["deeplink"], labels)
        if new != deeplink:
            deeplink, since = new, now if new else None
    elif url != r.url and deeplink:
        warnings.append("Диплинк был создан в кабинете Ozon для прежних меток — он убран. Создайте новый в кабинете и "
                        "вставьте сюда")
        deeplink, since = None, None
    elif deeplink:
        tl.check_deeplink(deeplink, labels)  # макросы с диплинком нельзя
    substitute = tl.check_substitute(sent["substitute_code"]) if "substitute_code" in sent else r.substitute_code

    if (url, deeplink, substitute) == (r.url, r.deeplink, r.substitute_code):
        return LinkSaved(link=await get_row(db, store_id, link_id, now), warnings=warnings)
    r.superseded_at = now
    db.add(_next(r, now, campaign=labels.campaign, campaign_name=tl.campaign_name_of(labels.campaign),
                 utm_source=labels.source, utm_medium=labels.medium, utm_term=labels.term, utm_content=labels.content,
                 url=url, deeplink=deeplink, deeplink_since=since, substitute_code=substitute))
    await db.commit()
    return LinkSaved(link=await get_row(db, store_id, link_id, now), warnings=warnings)


async def delete(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID) -> None:
    """Удаление — отметка у действующей версии; строки остаются (правило 12)."""
    r = await _current_of(db, store_id, link_id)
    if r.deleted_at is None:
        r.deleted_at = _now()
        await db.commit()


async def restore(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID) -> LinkRow:
    """«Вернуть» — новая версия без отметки удаления."""
    now = _now()
    r = await _current_of(db, store_id, link_id)
    if r.deleted_at is not None:
        r.superseded_at = now
        db.add(_next(r, now))
        await db.commit()
    return await get_row(db, store_id, link_id, now)


async def opened(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID) -> LinkRow:
    """Пользователь открыл ссылку с диплинком из сервиса — переход продлевает её на 30 дней."""
    r = await _current_of(db, store_id, link_id)
    if not r.deeplink:
        raise tl.LinkError("У ссылки нет диплинка — продлевать нечего: обычные ссылки работают бессрочно")
    db.add(TrafficLinkOpen(user_id=store_id, link_id=link_id, opened_at=_now()))
    await db.commit()
    return await get_row(db, store_id, link_id)


async def versions(db: AsyncSession, store_id: uuid.UUID, link_id: uuid.UUID) -> list[LinkVersion]:
    rows = (await db.scalars(select(TrafficLink).where(
        TrafficLink.user_id == store_id, TrafficLink.link_id == link_id).order_by(TrafficLink.version.desc()))).all()
    if not rows:
        raise NotFound
    return [LinkVersion(version=r.version, created_at=_utc(r.created_at), campaign=r.campaign, utm_source=r.utm_source,
                        utm_medium=r.utm_medium, utm_term=r.utm_term, utm_content=r.utm_content, url=r.url,
                        deeplink=r.deeplink, substitute_code=r.substitute_code, deleted_at=_utc(r.deleted_at),
                        superseded_at=_utc(r.superseded_at)) for r in rows]


# ---------- выгрузка ----------

_COLUMNS = [
    Col("Создана", 12, "date"), Col("Что размечено", 40), Col("Артикул", 16), Col("Раздел Ozon", 16),
    Col("Ссылка с метками", 90), Col("utm_source", 14), Col("utm_medium", 14), Col("utm_campaign", 34),
    Col("utm_term", 14), Col("utm_content", 14), Col("Диплинк", 40), Col("Диплинк работает до", 14, "date"),
    Col("Подменный артикул", 14),
]


def to_xlsx(items: list[LinkRow]) -> bytes:
    rows = [[
        r.created_at.date(), r.product.name if r.product and r.product.name else r.source_url,
        r.product.offer_id if r.product else "", tl.SECTIONS.get(r.section, r.section), r.url, r.utm_source,
        r.utm_medium, r.campaign, r.utm_term, r.utm_content, r.deeplink or "",
        r.deeplink_status.expires_on if r.deeplink_status else None, r.substitute_code or "",
    ] for r in items]
    notes = ["Ссылки внешнего трафика с UTM-метками",
             "Диплинк перестаёт вести на страницу через 30 дней без переходов — откройте ссылку до этой даты. "
             "Ссылки без диплинка работают бессрочно."]
    return table_xlsx("Ссылки", _COLUMNS, rows, notes=notes)
