"""Реферальная программа (шаг 11.7): настройки программы магазина, участники и их личные ссылки, отметка «отправлено».
Ссылка участника — запись истории ссылок 11.2 (видна и в «Созданных ссылках»), код — в utm_content."""
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import referral as eng
from app.engine import traffic_links as tl
from app.models.product import Product
from app.models.referral import ReferralEvent, ReferralParticipant, ReferralProgram
from app.models.traffic import TrafficLink
from app.schemas.referral import (
    Participant,
    ParticipantCounts,
    ParticipantCreate,
    ParticipantEvent,
    ParticipantList,
    ParticipantUpdate,
    ProgramOut,
    ProgramUpdate,
    ReferralSettings,
    Sent,
    SentIn,
)
from app.schemas.traffic import ProductRef
from app.services import traffic as traffic_svc
from app.services import traffic_history
from app.services.sync.finance import today_msk
from app.services.xlsx_export import Col, table_xlsx

TABS = ("all", "new", "sent", "deleted")
EVENT_TITLES = {
    eng.CREATED: "Добавлен, ссылка создана", eng.SENT: "Ссылка отправлена", eng.UNSENT: "Отметка «отправлено» снята",
    eng.DELETED: "Убран из программы", eng.RESTORED: "Возвращён в программу",
}


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


# ---------- настройки ----------


def _program(row: ReferralProgram | None) -> eng.Program:
    if row is None:
        return eng.Program()
    return eng.Program(row.campaign_name, row.utm_source, row.utm_medium, row.store_url)


async def settings(db: AsyncSession, store_id: uuid.UUID) -> ReferralSettings:
    row = await db.get(ReferralProgram, store_id)
    p = _program(row)
    prefix = await traffic_svc.get_prefix(db, store_id)
    return ReferralSettings(
        program=ProgramOut(campaign_name=p.campaign_name, utm_source=p.utm_source, utm_medium=p.utm_medium,
                           store_url=p.store_url, defaults=row is None),
        utm_prefix=prefix, campaign=f"{prefix}_{p.campaign_name}" if prefix else None, channels=eng.SENT_CHANNELS,
    )


async def save_settings(db: AsyncSession, store_id: uuid.UUID, data: ProgramUpdate) -> ReferralSettings:
    """Новые метки — только для новых участников: отправленные ссылки не меняются."""
    p = eng.check_program(data.campaign_name, data.utm_source, data.utm_medium, data.store_url)
    row = await db.get(ReferralProgram, store_id) or ReferralProgram(user_id=store_id)
    row.campaign_name, row.utm_source, row.utm_medium, row.store_url = (
        p.campaign_name, p.utm_source, p.utm_medium, p.store_url)
    db.add(row)
    await db.commit()
    return await settings(db, store_id)


# ---------- участники ----------


async def create(db: AsyncSession, store_id: uuid.UUID, data: ParticipantCreate) -> Participant:
    """Участник и его личная ссылка: на товар из отзыва (по SKU) или на магазин (ссылка из настроек программы)."""
    name = eng.check_name(data.name)
    prefix = await traffic_svc.get_prefix(db, store_id)
    if not prefix:
        raise eng.ReferralError("Сначала укажите префикс для метки UTM_CAMPAIGN — без него Ozon не засчитает переходы "
                                "по ссылкам (Настройки → Продвижение)")
    program = _program(await db.get(ReferralProgram, store_id))
    product = None
    if data.product_id:
        product = await db.get(Product, data.product_id)
        if product is None or product.user_id != store_id:
            raise eng.ReferralError("Товар не найден в каталоге магазина — выберите его заново")
    if data.target == eng.PRODUCT:
        if product is None:
            raise eng.ReferralError("Выберите товар из отзыва — ссылка поведёт на него. Или ведите ссылку на магазин")
        if not product.ozon_sku:
            raise eng.ReferralError("У товара нет SKU Ozon — обновите товары (кнопка «Синхронизация») или ведите ссылку "
                                    "на магазин")
        target = tl.parse_link(tl.product_url(product.ozon_sku))
    else:
        if not program.store_url:
            raise eng.ReferralError("Укажите ссылку на магазин в настройках программы — например "
                                    "https://www.ozon.ru/seller/имя/")
        target = tl.parse_link(program.store_url)
    taken = set((await db.scalars(select(ReferralParticipant.code).where(ReferralParticipant.user_id == store_id))).all())
    code = eng.new_code(taken)
    now = _now()
    link = traffic_history.add_link(db, store_id, target, eng.labels(prefix, program, code), channel="other",
                                    product_id=product.id if product else None, now=now)
    p = ReferralParticipant(user_id=store_id, code=code, name=name, product_id=product.id if product else None,
                            review_date=data.review_date, note=(data.note or "").strip() or None, target=data.target,
                            link_id=link.link_id, created_at=now)
    db.add(p)
    await db.flush()
    db.add(ReferralEvent(user_id=store_id, participant_id=p.id, kind=eng.CREATED, created_at=now))
    await db.commit()
    return await get(db, store_id, p.id)


async def _one(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> ReferralParticipant:
    p = await db.get(ReferralParticipant, pid)
    if p is None or p.user_id != store_id:
        raise NotFound
    return p


async def _rows(db: AsyncSession, store_id: uuid.UUID, parts: list[ReferralParticipant]) -> list[Participant]:
    if not parts:
        return []
    ids = [p.id for p in parts]
    events: dict[uuid.UUID, list[ReferralEvent]] = {}
    for ev in (await db.scalars(select(ReferralEvent).where(
            ReferralEvent.user_id == store_id, ReferralEvent.participant_id.in_(ids))
            .order_by(ReferralEvent.created_at, ReferralEvent.id))).all():
        events.setdefault(ev.participant_id, []).append(ev)
    links = {r.link_id: r for r in (await db.scalars(select(TrafficLink).where(
        TrafficLink.user_id == store_id, TrafficLink.link_id.in_([p.link_id for p in parts]),
        TrafficLink.superseded_at.is_(None)))).all()}
    pids = {p.product_id for p in parts if p.product_id}
    products = {x.id: x for x in (await db.scalars(select(Product).where(Product.id.in_(pids)))).all()} if pids else {}
    out = []
    for p in parts:
        evs = events.get(p.id, [])
        is_sent, last = eng.sent_state([(e.kind, e) for e in sorted(evs, key=lambda e: _utc(e.created_at))])
        prod = products.get(p.product_id) if p.product_id else None
        link = links.get(p.link_id)
        out.append(Participant(
            id=p.id, code=p.code, name=p.name,
            product=ProductRef(id=prod.id, offer_id=prod.offer_id, name=prod.name, sku=prod.ozon_sku) if prod else None,
            review_date=p.review_date, note=p.note, target=p.target, link_id=p.link_id, url=link.url if link else "",
            status="sent" if is_sent else "new",
            sent=Sent(channel=last.channel, channel_title=eng.SENT_CHANNELS.get(last.channel, last.channel),
                      sent_on=last.sent_on, note=last.note, marked_at=_utc(last.created_at)) if last else None,
            created_at=_utc(p.created_at), deleted=p.deleted_at is not None,
        ))
    return out


async def get(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> Participant:
    return (await _rows(db, store_id, [await _one(db, store_id, pid)]))[0]


def _fold(v) -> str:
    return "" if v is None else " ".join(str(v).split()).lower().replace("ё", "е")


def _tab_ok(p: Participant, tab: str) -> bool:
    if tab == "deleted":
        return p.deleted
    if p.deleted:
        return False
    return tab == "all" or p.status == tab


async def list_participants(
    db: AsyncSession, store_id: uuid.UUID, *, tab: str = "all", search: str | None = None,
    date_from: date | None = None, date_to: date | None = None, channel: str | None = None,
    product_id: uuid.UUID | None = None,
) -> ParticipantList:
    """Участники магазина, новые сверху. Счётчики вкладок — с учётом поиска, периода и фильтров."""
    parts = list((await db.scalars(select(ReferralParticipant).where(ReferralParticipant.user_id == store_id))).all())
    rows = await _rows(db, store_id, parts)
    q = _fold(search)

    def fits(r: Participant) -> bool:
        day = r.created_at.date()
        if date_from and day < date_from or date_to and day > date_to:
            return False
        if channel and (r.sent is None or r.sent.channel != channel):
            return False
        if product_id and (r.product is None or r.product.id != product_id):
            return False
        if q:
            hay = (r.name, r.code, r.note, r.url, *(() if not r.product else (r.product.offer_id, r.product.name,
                                                                                r.product.sku)))
            return any(q in _fold(v) for v in hay)
        return True

    found = [r for r in rows if fits(r)]
    counts = ParticipantCounts(**{t: sum(1 for r in found if _tab_ok(r, t)) for t in TABS})
    items = sorted((r for r in found if _tab_ok(r, tab)), key=lambda r: r.created_at, reverse=True)
    return ParticipantList(items=items, total=len(items), counts=counts)


async def update(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID, data: ParticipantUpdate) -> Participant:
    """Имя, дата отзыва, заметка — на месте (справочник; запрос — в журнале действий). Ссылка не меняется."""
    p = await _one(db, store_id, pid)
    sent = data.model_dump(exclude_unset=True)
    if "name" in sent:
        p.name = eng.check_name(sent["name"])
    if "review_date" in sent:
        p.review_date = sent["review_date"]
    if "note" in sent:
        p.note = (sent["note"] or "").strip() or None
    await db.commit()
    return await get(db, store_id, pid)


async def _event(db: AsyncSession, store_id: uuid.UUID, p: ReferralParticipant, kind: str, **fields) -> None:
    db.add(ReferralEvent(user_id=store_id, participant_id=p.id, kind=kind, created_at=_now(), **fields))


async def mark_sent(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID, data: SentIn) -> Participant:
    """«Отправлено» — новое событие (повторная отметка — тоже: например, отправили ещё раз в другой канал)."""
    p = await _one(db, store_id, pid)
    if p.deleted_at is not None:
        raise eng.ReferralError("Участник убран из программы — сначала верните его")
    day = data.sent_on or today_msk()
    if day > today_msk():
        raise eng.ReferralError("Дата отправки — не позже сегодняшней")
    await _event(db, store_id, p, eng.SENT, channel=data.channel, sent_on=day, note=(data.note or "").strip() or None)
    await db.commit()
    return await get(db, store_id, pid)


async def unmark_sent(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> Participant:
    p = await _one(db, store_id, pid)
    if (await get(db, store_id, pid)).status == "sent":
        await _event(db, store_id, p, eng.UNSENT)
        await db.commit()
    return await get(db, store_id, pid)


async def delete(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> None:
    """Убрать из программы — отметка; ссылка остаётся в «Созданных ссылках»: её уже могли отправить."""
    p = await _one(db, store_id, pid)
    if p.deleted_at is None:
        p.deleted_at = _now()
        await _event(db, store_id, p, eng.DELETED)
        await db.commit()


async def restore(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> Participant:
    p = await _one(db, store_id, pid)
    if p.deleted_at is not None:
        p.deleted_at = None
        await _event(db, store_id, p, eng.RESTORED)
        await db.commit()
    return await get(db, store_id, pid)


async def events(db: AsyncSession, store_id: uuid.UUID, pid: uuid.UUID) -> list[ParticipantEvent]:
    await _one(db, store_id, pid)
    rows = (await db.scalars(select(ReferralEvent).where(
        ReferralEvent.user_id == store_id, ReferralEvent.participant_id == pid)
        .order_by(ReferralEvent.created_at.desc(), ReferralEvent.id))).all()
    return [ParticipantEvent(kind=e.kind, title=EVENT_TITLES.get(e.kind, e.kind),
                             channel=eng.SENT_CHANNELS.get(e.channel) if e.channel else None, sent_on=e.sent_on,
                             note=e.note, created_at=_utc(e.created_at)) for e in rows]


# ---------- выгрузка ----------

_COLUMNS = [
    Col("Добавлен", 12, "date"), Col("Участник", 28), Col("Код", 10), Col("Товар из отзыва", 36), Col("Артикул", 16),
    Col("Дата отзыва", 12, "date"), Col("Личная ссылка", 90), Col("Статус", 14), Col("Отправлена", 12, "date"),
    Col("Где отправлена", 24), Col("Заметка", 40),
]


def to_xlsx(items: list[Participant]) -> bytes:
    rows = [[
        r.created_at.date(), r.name, r.code, r.product.name if r.product else "", r.product.offer_id if r.product else "",
        r.review_date, r.url, "Убран" if r.deleted else "Отправлена" if r.status == "sent" else "Не отправлена",
        r.sent.sent_on if r.sent else None, r.sent.channel_title if r.sent else "", r.note or "",
    ] for r in items]
    notes = ["Реферальная программа: участники и их личные ссылки",
             "Код участника — в метке utm_content (ref_<код>): по нему в отчёте Ozon «по заказам» видно покупки друзей."]
    return table_xlsx("Участники", _COLUMNS, rows, notes=notes)
