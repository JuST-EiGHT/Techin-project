"""Банки по API (шаг 3.12): подключение по токену, проверка доступа, обновление выписок.

- Токен проверяем до сохранения: банк должен его принять и показать рублёвые счета. Храним только Fernet-шифротекст,
  не логируем и не возвращаем (правило 7). Права — только чтение.
- Тот же банк и те же счета — новое подключение заменяет токен прежнего (выпустили новый взамен истёкшего).
- Обновление: при первом — история, насколько её отдаёт банк; дальше — с последнего загруженного дня минус 3 дня
  (операции, проведённые задним числом). Выписка банка собирается в ту же структуру, что файл 1С, и проходит тот же путь
  (`bank_import`): без дублей (в том числе с операциями из файла), поступление от Ozon закрывает выплату Ozon, переводы
  между своими счетами, правила разнесения.
- Токен не принят (401 / 403) — подключение в статусе «нужен новый токен», по расписанию его не дёргаем.
- Отключение удаляет токен; загруженные операции остаются (правило 12).
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.bank import PROVIDERS, make_provider
from app.integrations.bank.base import AccountStatement, BankAccountInfo, BankAPIError, BankAuthError, BankProvider
from app.integrations.bank.statement_1c import AccountSection, Document, Statement
from app.models.bank import BankConnection, BankTransaction
from app.models.user import User
from app.services import bank_import
from app.services import planning as planning_svc
from app.services.encryption import DecryptionError, decrypt, encrypt
from app.services.sync.finance import today_msk

OVERLAP_DAYS = 3
PROVIDER_OPTIONS: dict = {}  # параметры провайдера (в тестах — без пауз)


class BankConnectionError(Exception):
    """Подключение не сохранено или не найдено. Текст — для пользователя."""


class NotFound(BankConnectionError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def masked(number: str) -> str:
    return f"···{number[-4:]}"


def provider_for(code: str, token: str) -> BankProvider:
    return make_provider(code, token, **PROVIDER_OPTIONS)


def _accounts_json(accounts: list[BankAccountInfo]) -> list[dict]:
    return [{"number": a.number, "name": a.name} for a in accounts]


async def list_connections(db: AsyncSession, user_id: uuid.UUID) -> list[BankConnection]:
    res = await db.execute(select(BankConnection).where(BankConnection.user_id == user_id)
                           .order_by(BankConnection.created_at))
    return list(res.scalars())


async def get(db: AsyncSession, user_id: uuid.UUID, conn_id: uuid.UUID) -> BankConnection:
    conn = await db.get(BankConnection, conn_id)
    if conn is None or conn.user_id != user_id:
        raise NotFound("Подключение банка не найдено")
    return conn


async def connect(db: AsyncSession, user_id: uuid.UUID, provider: str, token: str) -> tuple[BankConnection, bool]:
    """Проверить токен и сохранить. (подключение, заменён ли токен прежнего подключения). BankAPIError — банк не принял."""
    if provider not in PROVIDERS:
        raise BankConnectionError("Этот банк пока не подключается по API — загрузите выписку файлом в «Планировании» → «Счета»")
    token = (token or "").strip()
    if not token:
        raise BankConnectionError("Вставьте токен из интернет-банка")
    async with provider_for(provider, token) as p:
        access = await p.check_access()
    numbers = {a.number for a in access.accounts}
    for conn in await list_connections(db, user_id):
        overlap = numbers & {a["number"] for a in conn.accounts or []}
        if not overlap:
            continue
        if conn.provider != provider:
            raise BankConnectionError(f"Счёт {masked(min(overlap))} уже подключён через другой банк — проверьте токен")
        conn.token_encrypted = encrypt(token)  # новый токен взамен прежнего — история и загруженные дни сохраняются
        conn.status, conn.status_message = "active", None
        conn.expires_at, conn.accounts = access.expires_at, _accounts_json(access.accounts)
        await db.flush()
        return conn, True
    conn = BankConnection(id=uuid.uuid4(), user_id=user_id, provider=provider, token_encrypted=encrypt(token),
                          status="active", expires_at=access.expires_at, accounts=_accounts_json(access.accounts), state={})
    db.add(conn)
    await db.flush()
    return conn, False


def _token(conn: BankConnection) -> str:
    try:
        return decrypt(conn.token_encrypted)
    except DecryptionError as exc:
        raise BankConnectionError("Не удалось прочитать сохранённый токен — отключите банк и подключите заново") from exc


async def verify(db: AsyncSession, user_id: uuid.UUID, conn_id: uuid.UUID) -> tuple[bool, str, BankConnection]:
    """Проверить доступ сейчас. Токен не принят — статус «нужен новый токен»; банк недоступен — статус не меняем."""
    conn = await get(db, user_id, conn_id)
    title = PROVIDERS[conn.provider].title
    try:
        async with provider_for(conn.provider, _token(conn)) as p:
            access = await p.check_access()
    except BankAuthError as exc:
        conn.status, conn.status_message = "auth", exc.message
        await db.flush()
        return False, exc.message, conn
    except BankAPIError as exc:
        return False, exc.message, conn
    conn.status, conn.status_message = "active", None
    conn.accounts = _accounts_json(access.accounts)
    conn.expires_at = access.expires_at or conn.expires_at
    await db.flush()
    return True, f"{title}: доступ есть, счетов — {len(access.accounts)}", conn


async def disconnect(db: AsyncSession, user_id: uuid.UUID, conn_id: uuid.UUID) -> None:
    """Удалить подключение вместе с токеном. Операции, счета и остатки банка остаются."""
    conn = await get(db, user_id, conn_id)
    await db.execute(update(BankTransaction).where(BankTransaction.connection_id == conn.id).values(connection_id=None))
    await db.delete(conn)
    await db.flush()


# ---------- обновление ----------


def statement_of(st: AccountStatement, source: str, opening: Decimal | None, closing: Decimal | None) -> Statement:
    """Выписка банка из API → та же структура, что у файла 1С."""
    acc = st.account
    docs = [
        Document(line=0, kind=op.doc_kind or "", number=op.doc_number, day=op.day, amount=op.amount, account=acc.number,
                 counterparty=op.counterparty, counterparty_inn=op.counterparty_inn,
                 counterparty_account=op.counterparty_account, counterparty_bik=op.counterparty_bik, purpose=op.purpose,
                 own_bik=acc.bik, own_bank=acc.bank_name, raw=op.raw, bank_id=op.bank_id)
        for op in sorted(st.operations, key=lambda o: (o.day, o.bank_id))
    ]
    section = AccountSection(acc.number, st.date_from, st.date_to, opening, closing, bik=acc.bik, bank_name=acc.bank_name)
    return Statement(None, "api", st.date_from, st.date_to, [section], docs, source=source)


async def sync_connection(db: AsyncSession, user_id: uuid.UUID, conn: BankConnection) -> dict:
    """Загрузить новые операции и остатки всех рублёвых счетов подключения. Коммиты — по счёту (apply)."""
    today = today_msk()
    stats = {"accounts": 0, "new": 0, "duplicate": 0, "ozon": 0, "transfer": 0, "warnings": []}
    async with provider_for(conn.provider, _token(conn)) as p:
        accounts = [a for a in await p.list_accounts() if a.rub]
        balances = await p.balances(accounts)
        numbers = {a.number for a in accounts}
        for acc in accounts:
            synced = ((conn.state or {}).get(acc.number) or {}).get("synced_to")
            date_from = date.fromisoformat(synced) - timedelta(days=OVERLAP_DAYS) if synced else p.history_start(today)
            date_from = min(date_from, today)
            st = await p.statement(acc, date_from, today)
            closing = st.closing if st.closing is not None else balances.get(acc.bank_id)
            opening = st.opening
            if opening is None and closing is not None and not synced:
                # банк не сообщил остаток на начало — считаем назад от остатка сейчас по операциям периода
                opening = closing - sum((op.amount for op in st.operations), Decimal(0))
            result = await bank_import.analyze_statement(db, user_id, statement_of(st, f"api_{p.code}", opening, closing),
                                                         own_extra=numbers)
            await bank_import.apply(db, user_id, result, connection_id=conn.id)
            stats["accounts"] += 1
            for key, status, kind in (("new", "new", None), ("duplicate", "duplicate", None), ("ozon", "new", "ozon"),
                                      ("transfer", "new", "transfer")):
                stats[key] += result.count(status, kind)
            stats["warnings"] += result.warnings
            conn.state = {**(conn.state or {}), acc.number: {"synced_to": today.isoformat()}}
        conn.accounts = _accounts_json(accounts)
    conn.status, conn.status_message, conn.last_sync_at = "active", None, _now()
    await db.commit()
    return stats


async def sync_user(db: AsyncSession, user_id: uuid.UUID, connection_id: str | None = None) -> dict:
    """Обновить все подключения пользователя (или одно). Токен не принят — подключение «нужен новый токен», остальные
    обновляются. Не обновилось ни одно — BankAPIError (запуск синхронизации — проваленный, с текстом банка)."""
    conns = await list_connections(db, user_id)
    if connection_id:
        conns = [c for c in conns if str(c.id) == str(connection_id)]
    else:
        conns = [c for c in conns if c.status != "auth"]  # токен не принят — ждём новый, банк зря не дёргаем
    out = {"connections": len(conns), "new": 0, "duplicate": 0, "ozon": 0, "transfer": 0, "errors": [], "warnings": []}
    errors: list[str] = []
    for conn_id, title in [(c.id, PROVIDERS[c.provider].title) for c in conns]:  # после отката объекты устаревают
        try:
            stats = await sync_connection(db, user_id, await get(db, user_id, conn_id))
        except (BankAPIError, BankConnectionError) as exc:
            await db.rollback()
            conn = await get(db, user_id, conn_id)
            message = getattr(exc, "message", str(exc))
            conn.status = "auth" if isinstance(exc, BankAuthError) else "error"
            conn.status_message = message
            await db.commit()
            errors.append(f"{title}: {message}" if not message.startswith(title) else message)
            continue
        for key in ("new", "duplicate", "ozon", "transfer"):
            out[key] += stats[key]
        out["warnings"] += stats["warnings"]
    out["errors"] = errors
    if conns:
        await planning_svc.sync_ozon_operations(db, user_id)  # поступления Ozon без пары → выплаты Ozon
    if conns and len(errors) == len(conns):
        raise BankAPIError(errors[0] if len(errors) == 1 else "; ".join(errors))
    return out


async def users_with_connections(db: AsyncSession) -> list[uuid.UUID]:
    """Работающие магазины с подключённым банком (отключённый магазин и закрытый аккаунт не обновляются)."""
    from app.services.stores import working

    res = await db.execute(
        select(BankConnection.user_id).join(User, User.id == BankConnection.user_id)
        .where(BankConnection.status != "auth", working()).distinct()
    )
    return list(res.scalars())
