"""Банки по API (шаг 3.12): подключить по токену, список, проверить доступ, обновить, отключить.
Токен принимается, но никогда не возвращается и не пишется в логи (правило 7)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import sync as sync_api
from app.db.session import get_db
from app.integrations.bank import PROVIDERS
from app.integrations.bank.base import BankAPIError, BankAuthError
from app.models.bank import BankConnection
from app.models.user import User
from app.schemas.bank import BankConnectionOut, ConnectIn, ConnectResult, VerifyResult
from app.schemas.sync import SyncRunResponse, utc
from app.services import bank_connections as svc
from app.services.stores import get_current_store
from app.services.sync.runner import SyncAlreadyRunning

router = APIRouter(prefix="/bank/connections", tags=["bank"])


def out(conn: BankConnection) -> BankConnectionOut:
    return BankConnectionOut(
        id=conn.id, provider=conn.provider, title=PROVIDERS[conn.provider].title, status=conn.status,
        status_message=conn.status_message, expires_at=utc(conn.expires_at),
        accounts=[{"number": svc.masked(a["number"]), "name": a.get("name")} for a in conn.accounts or []],
        last_sync_at=utc(conn.last_sync_at), created_at=utc(conn.created_at),
    )


async def _queue(db: AsyncSession, user_id: uuid.UUID, connection_id: uuid.UUID | None = None):
    params = {"connection_id": str(connection_id)} if connection_id else None
    return await sync_api.queue_sync(db, user_id, "bank", params)


@router.get("", response_model=list[BankConnectionOut])
async def list_connections(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return [out(c) for c in await svc.list_connections(db, user.id)]


@router.post("", response_model=ConnectResult, status_code=status.HTTP_201_CREATED)
async def connect(data: ConnectIn, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Проверить токен в банке и сохранить; сразу ставим загрузку истории операций."""
    try:
        conn, replaced = await svc.connect(db, user.id, data.provider, data.token.get_secret_value())
    except BankAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Банк не подключён: {exc.message}") from None
    except BankAPIError as exc:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if exc.status_code is None or exc.status_code >= 429 \
            else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, f"Банк не подключён: {exc.message}") from None
    except svc.BankConnectionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    await db.commit()
    queued = True
    try:
        await _queue(db, user.id, conn.id)
    except (SyncAlreadyRunning, sync_api.QueueUnavailable):
        queued = False  # догрузит обновление по расписанию
    return ConnectResult(connection=out(conn), replaced=replaced, sync_queued=queued)


@router.post("/sync", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_all(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """«Обновить»: новые операции и остатки всех подключённых банков."""
    if not await svc.list_connections(db, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Банк не подключён — подключите его в меню аккаунта → «Банки»")
    return await _run(db, user.id)


async def _run(db: AsyncSession, user_id: uuid.UUID, connection_id: uuid.UUID | None = None) -> SyncRunResponse:
    try:
        run = await _queue(db, user_id, connection_id)
    except SyncAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, "Обновление банка уже идёт, дождитесь окончания") from None
    except sync_api.QueueUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, sync_api.QUEUE_UNAVAILABLE) from None
    return SyncRunResponse.from_model(run)


@router.post("/{conn_id}/sync", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_one(conn_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    try:
        await svc.get(db, user.id, conn_id)
    except svc.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return await _run(db, user.id, conn_id)


@router.post("/{conn_id}/verify", response_model=VerifyResult)
async def verify(conn_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Проверить доступ сейчас: токен не принят — подключение «нужен новый токен»."""
    try:
        ok, message, conn = await svc.verify(db, user.id, conn_id)
    except svc.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except svc.BankConnectionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    await db.commit()
    return VerifyResult(ok=ok, message=message, connection=out(conn))


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(conn_id: uuid.UUID, user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Отключить: токен удаляется, загруженные операции и счета остаются."""
    try:
        await svc.disconnect(db, user.id, conn_id)
    except svc.NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
