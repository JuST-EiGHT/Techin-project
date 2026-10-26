"""Команды администратора (шаг 8.4): `python -m app.admin <команда>` в контейнере api.

На сервере — через обёртку: `./scripts/admin.sh <команда>` (README-DEPLOY.md). Почты ещё нет (шаг 8.3a), поэтому
пользователей, пароли и приглашения администратор выдаёт здесь, а передаёт человеку сам (мессенджер, лично).

  create-user EMAIL [--name ИМЯ] [--ask]      создать пользователя; пароль — случайный (или --ask — ввести)
  reset-password EMAIL [--ask]                новый пароль; все сессии пользователя завершаются, блокировка входа снимается
  invite [--email EMAIL] [--note ДЛЯ_КОГО] [--days N]   приглашение на регистрацию: код и ссылка
  invites [--all]                             приглашения: действующие (или все)
  revoke-invite ID|КОД                        отозвать неиспользованное приглашение
  users                                       пользователи
  deactivate EMAIL / activate EMAIL           закрыть / вернуть доступ (данные остаются)
  unblock EMAIL                               снять блокировку входа после неудачных попыток
  resync EMAIL                                загрузить заново из Ozon данные всех магазинов аккаунта (90 дней)

Действия с пользователем пишутся в audit_log (action admin.*): журнал не меняется и не удаляется.
"""
import argparse
import asyncio
import getpass
import secrets
import sys
from collections.abc import Callable
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.crud import user as crud_user
from app.models.audit import OK
from app.models.invite import Invite
from app.models.user import User
from app.schemas.user import UserCreate
from app.models.user_session import ENDED_ALL, PASSWORD
from app.services import account, login_limit, registration
from app.services.audit import record
from app.services.security import hash_password

USER_ENTITY = "user"
AGENT = "admin-cli"
MODE_TITLES = {
    registration.CLOSED: "closed — регистрации нет, аккаунты создаёт администратор",
    registration.INVITE: "invite — регистрация только по приглашению",
    registration.OPEN: "open — регистрация открыта для всех",
}


class AdminError(Exception):
    """Команда не выполнена — текст для администратора."""


def new_password() -> str:
    return secrets.token_urlsafe(12)  # 16 знаков, ~96 бит


def _audit(db: AsyncSession, user: User, action: str, after: dict | None = None) -> None:
    entry = record(db, user.id, action=f"admin.{action}", entity_type=USER_ENTITY, entity_id=str(user.id), status=OK,
                   after=after)
    entry.user_agent = AGENT


async def _user(db: AsyncSession, email: str) -> User:
    user = await crud_user.get_user_by_email(db, email)
    if user is None:
        raise AdminError(f"Пользователь {email} не найден. Список — команда users")
    return user


def _password(args, ask: Callable[[str], str]) -> tuple[str, bool]:
    """Пароль и показывать ли его: введённый вручную не печатаем."""
    if not args.ask:
        return new_password(), True
    first = ask("Новый пароль (минимум 8 символов): ")
    if len(first) < 8:
        raise AdminError("Пароль: минимум 8 символов")
    if ask("Повторите пароль: ") != first:
        raise AdminError("Пароли не совпадают")
    return first, False


async def create_user(db: AsyncSession, args, out, ask) -> None:
    password, show = _password(args, ask)
    try:
        data = UserCreate(email=args.email, password=password, full_name=args.name)
    except ValidationError as e:
        raise AdminError("Email: некорректный адрес" if "email" in str(e) else str(e)) from e
    if await crud_user.get_user_by_email(db, data.email):
        raise AdminError(f"Пользователь {data.email} уже есть. Новый пароль — команда reset-password")
    user = await crud_user.create_user(db, data)
    user.is_verified = True  # адрес подтвердил администратор
    _audit(db, user, "create_user", {"email": user.email})
    await db.commit()
    out(f"Пользователь создан: {user.email}")
    if show:
        out(f"Пароль: {password}")
        out("Передайте его человеку лично; сменить пароль — меню аккаунта → «Аккаунт».")
    out(f"Вход: {settings.SITE_URL.rstrip('/')}/login")


async def reset_password(db: AsyncSession, args, out, ask) -> None:
    user = await _user(db, args.email)
    password, show = _password(args, ask)
    user.hashed_password = hash_password(password)
    user.tokens_valid_after = registration.utcnow()  # выход на всех устройствах
    _audit(db, user, "reset_password")
    await db.commit()
    await account.end_all(db, user.id, PASSWORD)  # и в списке «Активные сеансы» (шаг 10.26)
    await login_limit.reset_login(user.email)
    out(f"Пароль {user.email} изменён, все его сессии завершены, блокировка входа снята.")
    if show:
        out(f"Новый пароль: {password}")


async def invite(db: AsyncSession, args, out, _ask) -> None:
    if args.email:
        try:
            UserCreate(email=args.email, password="x" * 8)
        except ValidationError as e:
            raise AdminError("Email: некорректный адрес") from e
        if await crud_user.get_user_by_email(db, args.email):
            raise AdminError(f"Пользователь {args.email} уже зарегистрирован")
    days = args.days or settings.INVITE_DAYS
    if days < 1 or days > 90:
        raise AdminError("--days: от 1 до 90")
    item, code = await registration.create_invite(db, email=args.email, note=args.note, days=days)
    out(f"Приглашение создано, действует {days} дн. (до {item.expires_at:%d.%m.%Y %H:%M} UTC), один раз.")
    if item.email:
        out(f"Зарегистрироваться можно только с адресом {item.email}.")
    out(f"Код: {code}")
    out(f"Ссылка: {registration.invite_link(code)}")
    out("Код показан один раз — в базе хранится только его хэш.")
    if settings.registration_mode != registration.INVITE:
        out(f"Внимание: сейчас REGISTRATION_MODE={settings.registration_mode}, приглашение заработает при "
            f"REGISTRATION_MODE=invite в .env.prod (после правки — ./scripts/compose.sh up -d api).")


async def invites(db: AsyncSession, args, out, _ask) -> None:
    rows = (await db.scalars(select(Invite).order_by(Invite.created_at.desc()))).all()
    now = registration.utcnow()
    shown = [i for i in rows if args.all or registration.state(i, now) == registration.ACTIVE]
    out(f"Режим регистрации: {MODE_TITLES[settings.registration_mode]}")
    if not shown:
        out("Действующих приглашений нет." if not args.all else "Приглашений нет.")
        return
    emails = {u.id: u.email for u in (await db.scalars(select(User).where(
        User.id.in_([i.used_by for i in shown if i.used_by])))).all()}
    for i in shown:
        st = registration.state(i, now)
        line = (f"{str(i.id)[:8]}  …{i.code_tail}  {registration.STATE_TITLES[st]:<12}  "
                f"до {i.expires_at:%d.%m.%Y}  {i.email or 'любой email'}")
        if i.note:
            line += f"  ({i.note})"
        if i.used_by in emails:
            line += f"  → {emails[i.used_by]}"
        out(line)


async def revoke_invite(db: AsyncSession, args, out, _ask) -> None:
    target = args.target.strip()
    found = await registration.find_invite(db, target)
    if found is None and len(target) >= 6:
        rows = (await db.scalars(select(Invite))).all()
        matches = [i for i in rows if str(i.id).startswith(target.lower())]
        if len(matches) > 1:
            raise AdminError("Под это начало id подходит несколько приглашений — укажите id длиннее")
        found = matches[0] if matches else None
    if found is None:
        raise AdminError("Приглашение не найдено: укажите id из списка invites или сам код")
    st = registration.state(found)
    if st != registration.ACTIVE:
        raise AdminError(f"Приглашение уже {registration.STATE_TITLES[st]} — отзывать нечего")
    await registration.revoke(db, found.id)
    out(f"Приглашение {str(found.id)[:8]} (…{found.code_tail}) отозвано.")


async def users(db: AsyncSession, _args, out, _ask) -> None:
    rows = (await db.scalars(select(User).where(User.owner_id.is_(None)).order_by(User.created_at))).all()
    extra = dict((await db.execute(  # дополнительные магазины (шаг 8.4b) — строки без входа, в список людей не входят
        select(User.owner_id, func.count()).where(User.owner_id.is_not(None)).group_by(User.owner_id)
    )).all())
    out(f"Режим регистрации: {MODE_TITLES[settings.registration_mode]}")
    out(f"Пользователей: {len(rows)}")
    for u in rows:
        state = "активен" if u.is_active else "доступ закрыт"
        shops = f"  магазинов: {1 + extra[u.id]}" if extra.get(u.id) else ""
        out(f"{u.email:<40} {u.full_name or '—':<25} с {u.created_at:%d.%m.%Y}  {state}{shops}")


async def _set_active(db: AsyncSession, args, out, active: bool) -> None:
    user = await _user(db, args.email)
    if user.is_active == active:
        out(f"{user.email}: уже {'активен' if active else 'доступ закрыт'}.")
        return
    user.is_active = active
    if not active:
        user.tokens_valid_after = registration.utcnow()
    _audit(db, user, "activate" if active else "deactivate")
    await db.commit()
    if not active:
        await account.end_all(db, user.id, ENDED_ALL)
    out(f"{user.email}: доступ {'возвращён' if active else 'закрыт, сессии завершены; данные сохранены'}.")


async def deactivate(db: AsyncSession, args, out, _ask) -> None:
    await _set_active(db, args, out, False)


async def activate(db: AsyncSession, args, out, _ask) -> None:
    await _set_active(db, args, out, True)


async def unblock(db: AsyncSession, args, out, _ask) -> None:
    user = await _user(db, args.email)
    await login_limit.reset_login(user.email)
    out(f"Блокировка входа для {user.email} снята.")


RESYNC_KINDS = ("products", "stocks", "promotions", "postings", "supplies", "finance", "balance")


async def resync(db: AsyncSession, args, out, _ask) -> None:
    """Каждому работающему магазину аккаунта с ключом — загрузка из Ozon заново: товары (которых нет в кабинете —
    в архив), остатки, акции, отправления; начисления и баланс у Ozon — за 90 дней с заменой строк API (копии данных
    Ozon; отчёты XLSX, себестоимость, планирование не затрагиваются). Нужна после разделения ключей по магазинам (8.4b),
    когда данные двух кабинетов уже смешались."""
    from app.api.v1.sync import QueueUnavailable, queue_sync
    from app.crud.credential import has_active
    from app.services import stores
    from app.services.sync.finance import BACKFILL_DAYS, today_msk
    from app.services.sync.runner import SyncAlreadyRunning

    user = await _user(db, args.email)
    names = await stores.names(db, user.id)
    today = today_msk()
    period = {"date_from": (today - timedelta(days=BACKFILL_DAYS - 1)).isoformat(), "date_to": today.isoformat()}
    for store in await stores.stores_of(db, user, active_only=True):
        if not await has_active(db, store.id):
            out(f"«{names[store.id]}»: нет действующего ключа Ozon — пропущен.")
            continue
        done = []
        for kind in RESYNC_KINDS:
            try:
                await queue_sync(db, store.id, kind, period if kind in ("finance", "balance") else None)
                done.append(kind)
            except SyncAlreadyRunning:
                out(f"«{names[store.id]}»: {kind} уже загружается — повторите команду позже.")
            except QueueUnavailable as e:
                raise AdminError(f"Очередь задач недоступна: {e}") from e
        out(f"«{names[store.id]}»: в очереди — {', '.join(done) or 'ничего'}.")


COMMANDS = {
    "create-user": create_user, "reset-password": reset_password, "invite": invite, "invites": invites,
    "revoke-invite": revoke_invite, "users": users, "deactivate": deactivate, "activate": activate, "unblock": unblock,
    "resync": resync,
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="./scripts/admin.sh", description="Команды администратора Ozon Analytics",
                                epilog="Справка по команде: ./scripts/admin.sh <команда> --help")
    p._optionals.title = "параметры"
    sub = p.add_subparsers(dest="command", required=True, metavar="команда", title="команды")
    c = sub.add_parser("create-user", help="создать пользователя")
    c.add_argument("email")
    c.add_argument("--name", help="имя")
    c.add_argument("--ask", action="store_true", help="ввести пароль вручную вместо случайного")
    c = sub.add_parser("reset-password", help="задать новый пароль, завершить все сессии")
    c.add_argument("email")
    c.add_argument("--ask", action="store_true", help="ввести пароль вручную вместо случайного")
    c = sub.add_parser("invite", help="приглашение на регистрацию")
    c.add_argument("--email", help="только для этого адреса")
    c.add_argument("--note", help="для кого (видно только в списке invites)")
    c.add_argument("--days", type=int, help=f"сколько дней действует (по умолчанию {settings.INVITE_DAYS})")
    c = sub.add_parser("invites", help="список приглашений")
    c.add_argument("--all", action="store_true", help="все, а не только действующие")
    c = sub.add_parser("revoke-invite", help="отозвать приглашение")
    c.add_argument("target", help="id из списка invites (первые 8 знаков) или код")
    sub.add_parser("users", help="список пользователей")
    for name, text in (("deactivate", "закрыть доступ"), ("activate", "вернуть доступ"),
                       ("unblock", "снять блокировку входа"),
                       ("resync", "загрузить данные всех магазинов аккаунта из Ozon заново (90 дней)")):
        sub.add_parser(name, help=text).add_argument("email")
    for c in sub.choices.values():
        c._positionals.title, c._optionals.title = "аргументы", "параметры"
    return p


async def run(argv: list[str], sessions: async_sessionmaker[AsyncSession], out=print, ask=getpass.getpass) -> int:
    args = parser().parse_args(argv)
    async with sessions() as db:
        try:
            await COMMANDS[args.command](db, args, out, ask)
        except AdminError as e:
            out(f"Ошибка: {e}")
            return 1
    return 0


async def _main(argv: list[str]) -> int:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        return await run(argv, async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def main() -> int:
    return asyncio.run(_main(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
