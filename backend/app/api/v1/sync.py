"""Синхронизация с Ozon: ручной запуск и статус; «Обновить всё» и обзор по видам — кнопка «Синхронизация» (шаг 10.2)."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import credential as crud_credential
from app.db.session import get_db
from app.integrations.ozon.errors import OzonNotConfiguredError
from app.models.sync_run import SUCCESS, SyncRun
from app.models.user import User
from app.schemas.sync import (
    KindOverview, KindStatus, SyncAction, SyncAllItem, SyncAllRequest, SyncAllResponse, SyncRunResponse,
    SyncStatusResponse, utc,
)
from app.services import bank_connections
from app.services.stores import get_current_store
from app.services.sync import overview
from app.services.sync.runner import QUEUE, SyncAlreadyRunning, active_run, fail_run, latest_run, start_run

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])

QUEUE_UNAVAILABLE = "Фоновые задачи сейчас недоступны, попробуйте позже"


def enqueue_sync(kind: str, user_id: uuid.UUID, run_id: uuid.UUID, params: dict | None = None) -> None:
    """Ставит задачу в Celery. Вынесено отдельно — в тестах подменяется."""
    from app.tasks.sync import FOR_USER_TASKS

    FOR_USER_TASKS[kind].delay(str(user_id), str(run_id), **(params or {}))


class QueueUnavailable(Exception):
    pass


async def queue_sync(db: AsyncSession, user_id: uuid.UUID, kind: str, params: dict | None = None) -> SyncRun:
    """Создаёт запуск и ставит задачу в очередь. SyncAlreadyRunning — уже идёт; QueueUnavailable — нет брокера."""
    run = await start_run(db, user_id, kind)
    try:
        await run_in_threadpool(enqueue_sync, kind, user_id, run.id, params)
    except Exception as e:  # noqa: BLE001 — брокер недоступен
        log.exception("Не удалось поставить синхронизацию %s в очередь", kind)
        await fail_run(db, run, QUEUE_UNAVAILABLE, QUEUE)
        raise QueueUnavailable from e
    return run


async def _sync_now(kind: str, user: User, db: AsyncSession, params: dict | None = None) -> SyncRunResponse:
    if not await crud_credential.has_active(db, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, OzonNotConfiguredError().message)
    try:
        run = await queue_sync(db, user.id, kind, params)
    except SyncAlreadyRunning:
        raise HTTPException(status.HTTP_409_CONFLICT, "Синхронизация уже идёт, дождитесь окончания") from None
    except QueueUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_UNAVAILABLE) from None
    return SyncRunResponse.from_model(run)


@router.post("/products", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_products_now(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return await _sync_now("products", user, db)


@router.post("/stocks", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_stocks_now(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return await _sync_now("stocks", user, db)


@router.post("/balance", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_balance_now(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Баланс у Ozon и выплаты: вчера, сегодня и незагруженные дни за 90 дней."""
    return await _sync_now("balance", user, db)


@router.post("/promotions", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_promotions_now(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    return await _sync_now("promotions", user, db)


@router.post("/postings", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_postings_now(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Отправления FBO и FBS — продажи по кластерам и складам для планирования поставок (шаг 5.2)."""
    return await _sync_now("postings", user, db)


async def _kind_status(db: AsyncSession, user_id: uuid.UUID, kind: str) -> KindStatus:
    await active_run(db, user_id, kind)  # помечает зависшие запуски проваленными
    last = await latest_run(db, user_id, kind)
    success = await latest_run(db, user_id, kind, status=SUCCESS)
    return KindStatus(
        last_run=SyncRunResponse.from_model(last) if last else None,
        last_success_at=utc(success.finished_at) if success else None,
    )


def _kind_out(s: overview.KindState) -> KindOverview:
    p = s.problem
    return KindOverview(
        kind=s.kind, title=s.title, state=s.state, running=s.running, last_success_at=s.last_success_at,
        schedule=overview.SCHEDULE_TEXT[s.kind], problem=p.text if p else None, hint=p.hint if p else None,
        action=SyncAction(label=p.action[0], href=p.action[1]) if p and p.action else None,
        error=s.error, recent_until=s.recent_until, can_refresh=not s.blocked,
    )


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Статус каждого вида (как раньше — для страниц разделов) и обзор для кнопки «Синхронизация» (шаг 10.2)."""
    view = await overview.build(db, user.id)
    return SyncStatusResponse(
        has_active_key=await crud_credential.has_active(db, user.id),
        products=await _kind_status(db, user.id, "products"),
        stocks=await _kind_status(db, user.id, "stocks"),
        finance=await _kind_status(db, user.id, "finance"),
        promotions=await _kind_status(db, user.id, "promotions"),
        balance=await _kind_status(db, user.id, "balance"),
        has_bank=bool(await bank_connections.list_connections(db, user.id)),
        bank=await _kind_status(db, user.id, "bank"),
        postings=await _kind_status(db, user.id, "postings"),
        state=view.state,
        oldest_kind=view.oldest_kind,
        oldest_success_at=view.oldest_at,
        kinds=[_kind_out(s) for s in view.states],
    )


def _minutes(delta: timedelta) -> str:
    m = max(1, round(delta.total_seconds() / 60))
    return f"{m} мин"


def _all_message(items: list[SyncAllItem], now: datetime) -> str:
    def names(result: str) -> str:
        return ", ".join(i.title.lower() for i in items if i.result == result)

    parts = []
    if any(i.result == "queued" for i in items):
        parts.append(f"Обновляем: {names('queued')}.")
    if any(i.result == "running" for i in items):
        parts.append(f"Уже загружаются: {names('running')}.")
    recent = [i for i in items if i.result == "recent"]
    if recent:
        soon = min(i.recent_until for i in recent)
        parts.append(f"Обновлены недавно: {names('recent')} — снова можно через {_minutes(soon - now)}.")
    if any(i.result == "unavailable" for i in items):
        parts.append(f"Не удалось поставить в очередь: {names('unavailable')} — {QUEUE_UNAVAILABLE.lower()}.")
    return " ".join(parts)


@router.post("/all", response_model=SyncAllResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_all(
    data: SyncAllRequest = Body(default_factory=SyncAllRequest),
    user: User = Depends(get_current_store),
    db: AsyncSession = Depends(get_db),
):
    """«Обновить всё» (или один вид — kinds: [вид]): в очередь — виды текущего магазина, которые не загружаются сейчас и
    не обновлялись только что (overview.RECENT — бережём лимиты Ozon). Ключа Ozon нет — только банк."""
    has_key = await crud_credential.has_active(db, user.id)
    has_bank = bool([c for c in await bank_connections.list_connections(db, user.id) if c.status != "auth"])
    available = [k for k in overview.OZON_KINDS if has_key] + ([overview.BANK] if has_bank else [])
    if data.kinds is not None:
        unknown = [k for k in data.kinds if k not in overview.KINDS]
        if unknown or not data.kinds:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестный вид данных — обновите страницу")
        missing = [k for k in data.kinds if k not in available]
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, OzonNotConfiguredError().message if missing[0] != overview.BANK
                                else "Банк не подключён или ждёт новый токен — меню аккаунта → «Интеграции» → «Банки»")
        kinds = [k for k in overview.KINDS if k in data.kinds]
    else:
        kinds = available
    if not kinds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, OzonNotConfiguredError().message)

    now = datetime.now(timezone.utc)
    items = []
    for kind in kinds:
        title = overview.TITLES[kind]
        if await active_run(db, user.id, kind):
            items.append(SyncAllItem(kind=kind, title=title, result="running"))
            continue
        success = await latest_run(db, user.id, kind, status=SUCCESS)
        done = utc(success.finished_at) if success else None
        if done and done + overview.RECENT[kind] > now:
            items.append(SyncAllItem(kind=kind, title=title, result="recent", recent_until=done + overview.RECENT[kind]))
            continue
        try:
            run = await queue_sync(db, user.id, kind)
        except SyncAlreadyRunning:  # успели запустить между проверкой и запуском
            items.append(SyncAllItem(kind=kind, title=title, result="running"))
        except QueueUnavailable:
            items.append(SyncAllItem(kind=kind, title=title, result="unavailable"))
        else:
            items.append(SyncAllItem(kind=kind, title=title, result="queued", run_id=run.id))
    return SyncAllResponse(items=items, message=_all_message(items, now))
