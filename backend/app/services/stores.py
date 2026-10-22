"""Магазины аккаунта (шаг 8.4b): у одного человека — несколько магазинов Ozon, данные каждого — отдельно.

Магазин — строка users (см. модель): первый магазин — сам аккаунт человека, остальные — строки без входа с owner_id.
Все данные (товары, начисления, настройки, планирование…) привязаны к user_id магазина, поэтому код данных не знает о
магазинах: эндпоинты берут текущий магазин зависимостью `get_current_store` вместо `get_current_user`. Аккаунт
(профиль, пароль, Telegram, тема, список магазинов) — по-прежнему `get_current_user`.

Текущий магазин — заголовок `X-Store-Id` (его ставит прокси Next из cookie `store_id`); нет заголовка — первый магазин.
Чужой или отключённый магазин — 404: данные другого аккаунта неотличимы от несуществующих.

Один магазин — один кабинет Ozon (Client-Id): ключ другого кабинета в магазин не добавить — только новым магазином.
Магазины не удаляются (история данных — правило 12): отключённый пропадает из переключателя, его синхронизация
останавливается, данные сохраняются, его можно вернуть.
"""
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.session import get_db
from app.models.anomaly import Anomaly
from app.models.api_credential import ApiCredential
from app.models.product import Product
from app.models.user import User
from app.schemas.credential import DEFAULT_LABEL
from app.services.auth import get_current_user
from app.services.ozon_auth import mask_client_id

STORE_HEADER = "X-Store-Id"
AUDIT_USER = "audit_user_id"  # ключ в request.state: чей это запрос для журнала (магазин), его читает middleware
MAIN_NAME = "Основной магазин"
NOT_FOUND = "Магазин не найден — выберите магазин в шапке сайта"
MAX_STORES = 20  # на аккаунт: защита от случайного создания сотен строк


class StoreError(Exception):
    """Действие с магазином нельзя выполнить. Текст — для пользователя."""

    def __init__(self, message: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(message)
        self.code = code


async def get_current_store(
    request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    """Магазин, с данными которого работает запрос: из заголовка X-Store-Id, без него — первый (сам аккаунт)."""
    raw = (request.headers.get(STORE_HEADER) or "").strip()
    store = user
    if raw:
        try:
            store_id = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        if store_id != user.id:
            store = await db.get(User, store_id)
            if store is None or store.owner_id != user.id or not store.is_active:
                raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    setattr(request.state, AUDIT_USER, store.id)
    return store


def working():
    """Условие для фоновых задач: магазин работает — он включён, и аккаунт его владельца не закрыт."""
    owner = aliased(User)
    return and_(User.is_active.is_(True), or_(
        User.owner_id.is_(None), exists().where(owner.id == User.owner_id, owner.is_active.is_(True)),
    ))


async def account_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """Аккаунт человека, которому принадлежит магазин (у первого магазина — он сам)."""
    owner = await db.scalar(select(User.owner_id).where(User.id == user_id))
    return owner or user_id


async def store_ids(db: AsyncSession, account_id_: uuid.UUID) -> list[uuid.UUID]:
    """Все магазины аккаунта (и отключённые): сам аккаунт и строки с его owner_id."""
    return [account_id_, *(await db.scalars(select(User.id).where(User.owner_id == account_id_))).all()]


async def accounts_for(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """Магазин → аккаунт его владельца, одним запросом."""
    if not ids:
        return {}
    return {i: owner or i for i, owner in (await db.execute(select(User.id, User.owner_id).where(User.id.in_(ids)))).all()}


async def account_of(db: AsyncSession, store: User) -> User:
    """Аккаунт человека для магазина (у первого магазина — он сам)."""
    return store if store.owner_id is None else await db.get(User, store.owner_id)


async def stores_of(db: AsyncSession, account: User, *, active_only: bool = False) -> list[User]:
    """Магазины аккаунта: первый — сам аккаунт, дальше — по дате создания."""
    q = select(User).where(User.owner_id == account.id)
    if active_only:
        q = q.where(User.is_active.is_(True))
    return [account, *(await db.scalars(q.order_by(User.created_at, User.id))).all()]


async def names(db: AsyncSession, account_id_: uuid.UUID) -> dict[uuid.UUID, str]:
    """Названия всех магазинов аккаунта (для подписи в Telegram и журналах)."""
    account = await db.get(User, account_id_)
    if account is None:
        return {}
    return {s.id: display_name(s, n) for n, s in enumerate(await stores_of(db, account), start=1)}


def display_name(store: User, n: int) -> str:
    """Название: своё → у первого — «Основной магазин», у остальных — «Магазин N»."""
    if store.store_name:
        return store.store_name
    return MAIN_NAME if store.owner_id is None else f"Магазин {n}"


def _clean_name(name: str | None) -> str | None:
    name = " ".join((name or "").split())
    if len(name) > 100:
        raise StoreError("Название магазина — не длиннее 100 символов")
    return name or None


async def _unique_name(db: AsyncSession, account: User, name: str, skip: uuid.UUID | None = None) -> None:
    for n, s in enumerate(await stores_of(db, account), start=1):
        if s.id != skip and display_name(s, n).casefold() == name.casefold():
            raise StoreError(f"Магазин «{name}» уже есть — выберите другое название", status.HTTP_409_CONFLICT)


async def store_with_client(db: AsyncSession, account: User, client_id: str) -> User | None:
    """Магазин аккаунта, к которому уже подключён этот кабинет Ozon."""
    for s in await stores_of(db, account):
        if s.ozon_client_id == client_id:
            return s
    return None


async def check_key_for_store(db: AsyncSession, account: User, store: User, client_id: str) -> None:
    """Ключ можно добавить в магазин, только если он от его кабинета Ozon. Магазин без кабинета (ещё нет ключей или
    данных) принимает любой кабинет, который не подключён в другом магазине аккаунта. Ответ 409 — ключ от другого
    кабинета: интерфейс предлагает добавить его отдельным магазином; 400 — кабинет уже в другом магазине."""
    if store.ozon_client_id == client_id:
        return
    other = await store_with_client(db, account, client_id)
    names_ = await names(db, account.id)
    if other is not None:
        raise StoreError(
            f"Этот кабинет Ozon (Client-Id {mask_client_id(client_id)}) уже подключён в магазине «{names_[other.id]}» — "
            "переключитесь на него в шапке сайта",
        )
    if store.ozon_client_id is not None and await _has_data(db, store.id):
        raise StoreError(
            f"Этот ключ от другого кабинета Ozon (Client-Id {mask_client_id(client_id)}), а магазин «{names_[store.id]}» "
            f"работает с кабинетом {mask_client_id(store.ozon_client_id)}. Чтобы товары и деньги разных магазинов не "
            "смешались, добавьте его отдельным магазином", status.HTTP_409_CONFLICT,
        )


async def _has_data(db: AsyncSession, store_id: uuid.UUID) -> bool:
    """Есть ли у магазина ключи или загруженные товары — тогда его кабинет Ozon уже не сменить."""
    keys = await db.scalar(select(exists().where(ApiCredential.user_id == store_id)))
    return bool(keys or await db.scalar(select(exists().where(Product.user_id == store_id))))


def bind_client(store: User, client_id: str) -> None:
    """Магазин запоминает свой кабинет Ozon (первый ключ или смена кабинета у пустого магазина)."""
    store.ozon_client_id = client_id


async def new_store_name(db: AsyncSession, account: User, name: str | None, client_id: str, label: str | None) -> str:
    """Проверки нового магазина до обращения к Ozon: лимит, кабинет ещё не подключён, название свободно. Возвращает
    название: своё → название ключа → «Магазин N»."""
    stores = await stores_of(db, account)
    if len(stores) >= MAX_STORES:
        raise StoreError(f"У аккаунта уже {MAX_STORES} магазинов — больше добавить нельзя")
    if (other := await store_with_client(db, account, client_id)) is not None:
        raise StoreError(
            f"Этот кабинет Ozon уже подключён в магазине «{(await names(db, account.id))[other.id]}»"
            + (" (он отключён — верните его на странице «Магазины»)" if not other.is_active else ""),
            status.HTTP_409_CONFLICT,
        )
    name = _clean_name(name) or (label if label and label != DEFAULT_LABEL else None) or f"Магазин {len(stores) + 1}"
    await _unique_name(db, account, name)
    return name


async def create(db: AsyncSession, account: User, name: str | None, client_id: str, label: str | None) -> User:
    """Новый магазин аккаунта под кабинет Ozon. Коммит — на вызывающем (вместе с ключом)."""
    name = await new_store_name(db, account, name, client_id, label)
    store = User(owner_id=account.id, store_name=name, ozon_client_id=client_id, is_active=True, is_verified=False)
    db.add(store)
    await db.flush()
    return store


async def own(db: AsyncSession, account: User, store_id: uuid.UUID) -> User:
    if store_id == account.id:
        return account
    store = await db.get(User, store_id)
    if store is None or store.owner_id != account.id:
        raise StoreError(NOT_FOUND, status.HTTP_404_NOT_FOUND)
    return store


async def rename(db: AsyncSession, account: User, store: User, name: str | None) -> User:
    name = _clean_name(name)
    if name is None:
        raise StoreError("Укажите название магазина")
    await _unique_name(db, account, name, skip=store.id)
    store.store_name = name
    await db.commit()
    return store


async def set_active(db: AsyncSession, store: User, active: bool) -> User:
    """Отключить (данные сохраняются, синхронизация останавливается) или вернуть дополнительный магазин."""
    if store.owner_id is None:
        raise StoreError("Первый магазин — это сам аккаунт, его не отключить. Чтобы остановить загрузку данных, "
                         "удалите его ключ Ozon")
    store.is_active = active
    store.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return store


async def overview(db: AsyncSession, account: User) -> list[dict]:
    """Магазины для переключателя и страницы «Магазины»: название, кабинет, ключи, уведомления без «Ок»."""
    stores = await stores_of(db, account)
    ids = [s.id for s in stores]
    now = datetime.now(timezone.utc)
    keys: dict[uuid.UUID, dict] = {i: {"keys": 0, "active": 0} for i in ids}
    for c in (await db.scalars(select(ApiCredential).where(ApiCredential.user_id.in_(ids)))).all():
        keys[c.user_id]["keys"] += 1
        expires = c.expires_at if c.expires_at.tzinfo else c.expires_at.replace(tzinfo=timezone.utc)
        keys[c.user_id]["active"] += int(c.is_active and expires > now)
    alerts = dict((await db.execute(
        select(Anomaly.user_id, func.count()).where(
            Anomaly.user_id.in_(ids), Anomaly.resolved_at.is_(None), Anomaly.is_acknowledged.is_(False),
        ).group_by(Anomaly.user_id)
    )).all())
    return [{
        "id": s.id, "name": display_name(s, n), "is_main": s.owner_id is None, "is_active": s.is_active,
        "client_id_masked": mask_client_id(s.ozon_client_id) if s.ozon_client_id else None,
        "keys": keys[s.id]["keys"], "active_keys": keys[s.id]["active"], "unread_alerts": alerts.get(s.id, 0),
        "created_at": s.created_at,
    } for n, s in enumerate(stores, start=1)]
