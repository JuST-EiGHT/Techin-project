"""Аккаунт (шаг 10.26): сеансы входа, профиль, удаление с отсрочкой.

Сеансы. Вход — строка `user_sessions`, её номер (`sid`) — в токенах доступа и обновления. Обновление токена — та же
строка (последняя активность, адрес). Завершённый сеанс (выход, «Завершить», «Завершить все, кроме этого», смена пароля,
удаление аккаунта) не принимается ни при обновлении, ни на запросах: `check` в `get_current_user`. Токены без `sid`
(выданы до 10.26) работают до своего срока, а при обновлении получают сеанс.

Удаление. С паролем; отсрочка `DELETION_DAYS` — до неё аккаунт работает и удаление можно отменить, потом задача
(`purge_due`, раз в сутки) удаляет аккаунт и данные всех его магазинов. Это решение — DATA_HISTORY: правило «историю не
удаляем» действует внутри работающего аккаунта, а удалить свои данные целиком — право пользователя (закон о
персональных данных).
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import Base
from app.models.user import User
from app.models.user_session import ENDED, UserSession

DELETION_DAYS = 30
PHONE_RE = re.compile(r"^\+?\d{10,15}$")


class AccountError(Exception):
    """Текст — для пользователя."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# ── устройство по User-Agent ──
_BROWSERS = (("YaBrowser", "Яндекс Браузер"), ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
             ("Chrome/", "Chrome"), ("Safari/", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
            ("Mac OS X", "macOS"), ("Macintosh", "macOS"), ("Linux", "Linux"))


def device_label(user_agent: str | None) -> str:
    """«Chrome · Windows», «Safari · iPhone»; не распознали — «Неизвестное устройство»."""
    ua = user_agent or ""
    browser = next((name for key, name in _BROWSERS if key in ua), None)
    system = next((name for key, name in _SYSTEMS if key in ua), None)
    parts = [p for p in (browser, system) if p]
    return " · ".join(parts) if parts else "Неизвестное устройство"


# ── сеансы ──
def expires_at(s: UserSession) -> datetime:
    return _aware(s.last_seen_at) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def is_active(s: UserSession, now: datetime | None = None) -> bool:
    return s.ended_at is None and expires_at(s) > (now or utcnow())


async def start(db: AsyncSession, user_id: uuid.UUID, ip: str | None, user_agent: str | None) -> UserSession:
    now = utcnow()
    s = UserSession(id=uuid.uuid4(), user_id=user_id, created_at=now, last_seen_at=now, ip=ip,
                    user_agent=(user_agent or None) and user_agent[:500])
    db.add(s)
    await db.commit()
    return s


async def touch(db: AsyncSession, s: UserSession, ip: str | None, user_agent: str | None) -> None:
    s.last_seen_at = utcnow()
    if ip:
        s.ip = ip
    if user_agent:
        s.user_agent = user_agent[:500]
    await db.commit()


async def get(db: AsyncSession, sid: str | None) -> UserSession | None:
    if not sid:
        return None
    try:
        return await db.get(UserSession, uuid.UUID(sid))
    except ValueError:
        return None


async def active(db: AsyncSession, user_id: uuid.UUID) -> list[UserSession]:
    now = utcnow()
    rows = (await db.execute(select(UserSession).where(UserSession.user_id == user_id, UserSession.ended_at.is_(None))
                             .order_by(UserSession.last_seen_at.desc()))).scalars()
    return [s for s in rows if is_active(s, now)]


async def end(db: AsyncSession, user_id: uuid.UUID, sid: uuid.UUID, reason: str = ENDED) -> bool:
    s = await db.get(UserSession, sid)
    if s is None or s.user_id != user_id:
        raise AccountError("Сеанс не найден")
    if s.ended_at is not None:
        return False
    s.ended_at, s.end_reason = utcnow(), reason
    await db.commit()
    return True


async def end_all(db: AsyncSession, user_id: uuid.UUID, reason: str, keep: uuid.UUID | None = None) -> int:
    """Завершить все сеансы аккаунта (кроме `keep`). Сколько завершено."""
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.ended_at.is_(None))
    if keep is not None:
        q = q.where(UserSession.id != keep)
    result = await db.execute(q.values(ended_at=utcnow(), end_reason=reason))
    await db.commit()
    return result.rowcount or 0


# ── профиль ──
def split_name(full: str | None) -> tuple[str | None, str | None]:
    """«Имя Фамилия» (одно поле до 10.26, форма регистрации) → имя — до первого пробела, фамилия — остальное."""
    parts = (full or "").strip().split(maxsplit=1)
    return (parts[0][:100] if parts else None), (parts[1].strip()[:100] if len(parts) > 1 else None)


def full_name(first: str | None, last: str | None) -> str | None:
    return " ".join(p for p in (first, last) if p) or None


def clean_phone(raw: str | None) -> str | None:
    """«+7 (912) 345-67-89», «8 912 345 67 89» → «+79123456789»; пусто — None; не номер — ошибка."""
    if raw is None or not raw.strip():
        return None
    digits = re.sub(r"[\s()\-.]", "", raw.strip())
    if digits.startswith("8") and len(digits) == 11:
        digits = "+7" + digits[1:]
    if not PHONE_RE.match(digits):
        raise AccountError("Телефон: цифрами, например +7 912 345-67-89")
    return digits if digits.startswith("+") else "+" + digits


# ── удаление ──
async def request_deletion(db: AsyncSession, user: User, now: datetime | None = None) -> User:
    now = now or utcnow()
    if user.deletion_due_at is None:
        user.deletion_requested_at, user.deletion_due_at = now, now + timedelta(days=DELETION_DAYS)
    await db.commit()
    return user


async def cancel_deletion(db: AsyncSession, user: User) -> User:
    user.deletion_requested_at = user.deletion_due_at = None
    await db.commit()
    return user


async def purge(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Удалить аккаунт и данные всех его магазинов. Сколько магазинов (с самим аккаунтом) удалено.

    Таблицы с user_id — явно, от зависимых к главным (в Postgres то же сделали бы каскады; явно — и в SQLite тестов, и
    журнал действий: его триггер разрешает удаление только так, `app.audit_log_allow_delete` в этой транзакции)."""
    ids = [account_id, *(await db.execute(select(User.id).where(User.owner_id == account_id))).scalars()]
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SET LOCAL app.audit_log_allow_delete = 'on'"))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != "users" and "user_id" in table.c:
            await db.execute(delete(table).where(table.c.user_id.in_(ids)))
    await db.execute(delete(User).where(User.owner_id == account_id))
    await db.execute(delete(User).where(User.id == account_id))
    await db.commit()
    return len(ids)


async def purge_due(db: AsyncSession, now: datetime | None = None) -> list[uuid.UUID]:
    """Аккаунты, у которых отсрочка удаления кончилась, — удалить. Кого удалили."""
    now = now or utcnow()
    due = list((await db.execute(select(User.id).where(
        User.owner_id.is_(None), User.deletion_due_at.is_not(None), User.deletion_due_at <= now,
    ))).scalars())
    for account_id in due:
        await purge(db, account_id)
    return due
