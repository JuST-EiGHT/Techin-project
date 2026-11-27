"""«Продвижение → Реферальная программа» (шаг 11.7): тем, кто оставил отзыв, — личная ссылка с UTM-метками и кодом
участника; продавец отправляет её сам и отмечает «Отправлено». Покупки друзей — 11.8, награды промокодами — 11.9."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine import referral as eng
from app.engine import traffic_links as tl
from app.models.user import User
from app.schemas.referral import (
    Participant,
    ParticipantCreate,
    ParticipantEvent,
    ParticipantList,
    ParticipantUpdate,
    ProgramUpdate,
    ReferralSettings,
    SentIn,
)
from app.services import referral as svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/referral", tags=["referral"])

Tab = Literal["all", "new", "sent", "deleted"]
ERRORS = (eng.ReferralError, tl.LinkError)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _gone() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Участник не найден")


@router.get("/settings", response_model=ReferralSettings)
async def get_settings(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Метки личных ссылок программы, ссылка на магазин, префикс магазина, где можно отправить ссылку."""
    return await svc.settings(db, user.id)


@router.put("/settings", response_model=ReferralSettings)
async def put_settings(data: ProgramUpdate, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Новые метки — только для новых участников: уже отправленные ссылки не меняются."""
    try:
        return await svc.save_settings(db, user.id, data)
    except ERRORS as exc:
        raise _bad(exc) from exc


async def _list(db, user, tab, search, date_from, date_to, channel, product_id) -> ParticipantList:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период: дата начала позже даты окончания")
    return await svc.list_participants(db, user.id, tab=tab, search=search, date_from=date_from, date_to=date_to,
                                       channel=channel, product_id=product_id)


@router.get("/participants", response_model=ParticipantList)
async def list_participants(
    tab: Tab = Query("all", description="all · new — ссылка не отправлена · sent — отправлена · deleted — убраны"),
    search: str | None = Query(None, max_length=200, description="Имя, код, артикул, название, заметка"),
    date_from: date | None = Query(None, description="Добавлен с"),
    date_to: date | None = Query(None),
    channel: str | None = Query(None, max_length=16, description="Где отправлена ссылка"),
    product_id: uuid.UUID | None = Query(None),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    return await _list(db, user, tab, search, date_from, date_to, channel, product_id)


@router.get("/participants/export", response_class=Response)
async def export_participants(
    tab: Tab = Query("all"),
    search: str | None = Query(None, max_length=200),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    channel: str | None = Query(None, max_length=16),
    product_id: uuid.UUID | None = Query(None),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """Участники в Excel — те же, что на странице."""
    res = await _list(db, user, tab, search, date_from, date_to, channel, product_id)
    return attachment(svc.to_xlsx(res.items), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      f"Реферальная программа {today_msk():%d.%m.%Y}.xlsx", "referral.xlsx")


@router.post("/participants", response_model=Participant, status_code=status.HTTP_201_CREATED)
async def create_participant(data: ParticipantCreate, user: User = Depends(get_current_store),
                             db: AsyncSession = Depends(get_db)):
    """Добавить участника — сразу создаётся его личная ссылка (видна и в «Созданных ссылках»)."""
    try:
        return await svc.create(db, user.id, data)
    except ERRORS as exc:
        raise _bad(exc) from exc


@router.patch("/participants/{participant_id}", response_model=Participant)
async def update_participant(participant_id: uuid.UUID, data: ParticipantUpdate,
                             user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        return await svc.update(db, user.id, participant_id, data)
    except svc.NotFound as exc:
        raise _gone() from exc
    except ERRORS as exc:
        raise _bad(exc) from exc


@router.post("/participants/{participant_id}/sent", response_model=Participant)
async def mark_sent(participant_id: uuid.UUID, data: SentIn, user: User = Depends(get_current_store),
                    db: AsyncSession = Depends(get_db)):
    """Отметить, что ссылку отправили: где и когда."""
    try:
        return await svc.mark_sent(db, user.id, participant_id, data)
    except svc.NotFound as exc:
        raise _gone() from exc
    except ERRORS as exc:
        raise _bad(exc) from exc


@router.post("/participants/{participant_id}/unsent", response_model=Participant)
async def unmark_sent(participant_id: uuid.UUID, user: User = Depends(get_current_store),
                      db: AsyncSession = Depends(get_db)):
    try:
        return await svc.unmark_sent(db, user.id, participant_id)
    except svc.NotFound as exc:
        raise _gone() from exc


@router.delete("/participants/{participant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_participant(participant_id: uuid.UUID, user: User = Depends(get_current_store),
                             db: AsyncSession = Depends(get_db)):
    try:
        await svc.delete(db, user.id, participant_id)
    except svc.NotFound as exc:
        raise _gone() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/participants/{participant_id}/restore", response_model=Participant)
async def restore_participant(participant_id: uuid.UUID, user: User = Depends(get_current_store),
                              db: AsyncSession = Depends(get_db)):
    try:
        return await svc.restore(db, user.id, participant_id)
    except svc.NotFound as exc:
        raise _gone() from exc


@router.get("/participants/{participant_id}/events", response_model=list[ParticipantEvent])
async def participant_events(participant_id: uuid.UUID, user: User = Depends(get_current_store),
                             db: AsyncSession = Depends(get_db)):
    try:
        return await svc.events(db, user.id, participant_id)
    except svc.NotFound as exc:
        raise _gone() from exc
