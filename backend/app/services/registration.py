"""Регистрация по режиму сервиса и приглашения (шаг 8.4).

REGISTRATION_MODE: closed — регистрации нет, аккаунты создаёт администратор (`python -m app.admin create-user`);
invite — только с кодом приглашения (`python -m app.admin invite`); open — любой. Код приглашения показывается один раз
(в команде и в ссылке), в БД — только sha256; приглашение одноразовое и живёт INVITE_DAYS дней.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.invite import Invite
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.account import split_name
from app.services.security import hash_password

CLOSED, INVITE, OPEN = "closed", "invite", "open"
# 32 знака без похожих (нет I, O, 0, 1): 16 знаков — 80 бит, перебором не угадать
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 16

ACTIVE, USED, EXPIRED, REVOKED = "active", "used", "expired", "revoked"
STATE_TITLES = {ACTIVE: "действует", USED: "использовано", EXPIRED: "истекло", REVOKED: "отозвано"}

CLOSED_MESSAGE = "Регистрация закрыта: аккаунт создаёт администратор сервиса"


class InviteError(Exception):
    """Код приглашения не подошёл — текст для пользователя."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt  # SQLite — без пояса


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LEN, 4))


def normalize(code: str) -> str:
    """Регистр, пробелы и дефисы не важны: «k7qx 2m9p…» = «K7QX-2M9P-…»."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def code_hash(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def invite_link(code: str) -> str:
    return f"{settings.SITE_URL.rstrip('/')}/register?invite={code}"


def state(invite: Invite, now: datetime | None = None) -> str:
    now = now or utcnow()
    if invite.revoked_at is not None:
        return REVOKED
    if invite.used_at is not None:
        return USED
    if _aware(invite.expires_at) <= now:
        return EXPIRED
    return ACTIVE


async def create_invite(
    db: AsyncSession, *, email: str | None = None, note: str | None = None, days: int | None = None
) -> tuple[Invite, str]:
    """Новое приглашение и его код (код больше нигде не хранится — показать сразу)."""
    code = new_code()
    now = utcnow()
    invite = Invite(
        code_hash=code_hash(code), code_tail=normalize(code)[-4:], email=email.lower() if email else None,
        note=note, created_at=now, expires_at=now + timedelta(days=days or settings.INVITE_DAYS),
    )
    db.add(invite)
    await db.commit()
    return invite, code


async def find_invite(db: AsyncSession, code: str) -> Invite | None:
    return await db.scalar(select(Invite).where(Invite.code_hash == code_hash(code)))


async def check_invite(db: AsyncSession, code: str | None, email: str) -> Invite:
    """Приглашение, по которому можно зарегистрировать этот email, — или InviteError с понятной причиной."""
    if not code or not normalize(code):
        raise InviteError("Регистрация — по приглашению: укажите код из ссылки, которую прислал администратор")
    invite = await find_invite(db, code)
    if invite is None:
        raise InviteError("Код приглашения не найден — проверьте, что скопировали его целиком")
    st = state(invite)
    if st == USED:
        raise InviteError("Это приглашение уже использовано — попросите у администратора новое")
    if st == REVOKED:
        raise InviteError("Это приглашение отозвано — попросите у администратора новое")
    if st == EXPIRED:
        raise InviteError("Срок приглашения истёк — попросите у администратора новое")
    if invite.email and invite.email != email.lower():
        raise InviteError("Приглашение выдано на другой email — зарегистрируйтесь с тем адресом, на который его выдали")
    return invite


async def register(db: AsyncSession, data: UserCreate, invite: Invite | None) -> User:
    """Пользователь и отметка «приглашение использовано» — одной транзакцией: одно приглашение — один аккаунт, даже
    если два человека отправили код одновременно."""
    first, last = split_name(data.full_name)
    user = User(email=data.email.lower(), hashed_password=hash_password(data.password), full_name=data.full_name,
                first_name=first, last_name=last)
    db.add(user)
    await db.flush()
    if invite is not None:
        res = await db.execute(
            update(Invite)
            .where(Invite.id == invite.id, Invite.used_at.is_(None), Invite.revoked_at.is_(None))
            .values(used_at=utcnow(), used_by=user.id)
        )
        if res.rowcount != 1:
            await db.rollback()
            raise InviteError("Это приглашение уже использовано — попросите у администратора новое")
    await db.commit()
    await db.refresh(user)
    return user


async def revoke(db: AsyncSession, invite_id: uuid.UUID) -> Invite | None:
    invite = await db.get(Invite, invite_id)
    if invite is not None and invite.revoked_at is None and invite.used_at is None:
        invite.revoked_at = utcnow()
        await db.commit()
    return invite
