import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import user as crud_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_session import ENDED, ENDED_ALL, LOGOUT, PASSWORD
from app.schemas.user import (
    LogoutRequest,
    PasswordChange,
    PasswordConfirm,
    ProfileUpdate,
    RefreshRequest,
    RegistrationInfo,
    SessionOut,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.auth import (
    REFRESH,
    SESSION_CLOSED,
    SESSION_ENDED,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_token_payload,
    get_current_user,
    hash_password,
    issued_before_revocation,
    utcnow,
    verify_password,
)
from app.services import account, login_limit, registration
from app.services.audit import client_info
from app.services.token_blacklist import blacklist
from app.schemas.sync import utc

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user_id: uuid.UUID, sid: uuid.UUID | None = None) -> Token:
    return Token(
        access_token=create_access_token(user_id, sid),
        refresh_token=create_refresh_token(user_id, sid),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _too_many(seconds: int, what: str = "входа") -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, login_limit.wait_message(seconds, what), headers={"Retry-After": str(seconds)}
    )


@router.get("/registration", response_model=RegistrationInfo)
async def registration_info():
    """Режим регистрации — страницы входа и регистрации показывают ссылку, поле кода приглашения или «закрыто»."""
    return RegistrationInfo(mode=settings.registration_mode)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    mode = settings.registration_mode
    if mode == registration.CLOSED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, registration.CLOSED_MESSAGE)
    invite = None
    if mode == registration.INVITE:
        ip, _ = client_info(request)
        if wait := await login_limit.invite_wait(ip):
            raise _too_many(wait, "ввода кода приглашения")
        try:
            invite = await registration.check_invite(db, data.invite_code, data.email)
        except registration.InviteError as e:
            await login_limit.invite_failed(ip)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if await crud_user.get_user_by_email(db, data.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким email уже зарегистрирован")
    try:
        return await registration.register(db, data, invite)
    except registration.InviteError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/login", response_model=Token)
async def login(data: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    ip, _ = client_info(request)
    # Сверх лимита неудачных попыток пароль не проверяем вовсе — подбор упирается в окно
    if wait := await login_limit.login_wait(data.email, ip):
        raise _too_many(wait)
    user = await crud_user.get_user_by_email(db, data.email)
    # Одинаковый ответ для «нет пользователя» и «неверный пароль» — не раскрываем, какие email зарегистрированы
    if user is None or not verify_password(data.password, user.hashed_password):
        await login_limit.login_failed(data.email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт деактивирован")
    await login_limit.login_succeeded(data.email, ip)
    _, agent = client_info(request)
    session = await account.start(db, user.id, ip, agent)  # сеанс входа (шаг 10.26)
    return _issue_tokens(user.id, session.id)


@router.post("/refresh", response_model=Token)
async def refresh(data: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(data.refresh_token, expected_type=REFRESH)
    except InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    if await blacklist.is_revoked(payload["jti"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh-токен уже использован или отозван")

    user = await crud_user.get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт деактивирован")
    if issued_before_revocation(user, payload):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_ENDED)
    ip, agent = client_info(request)
    session = await account.get(db, payload.get("sid"))
    if payload.get("sid") and (session is None or session.ended_at is not None or session.user_id != user.id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_CLOSED)

    # Ротация: старый refresh больше не работает
    await blacklist.revoke(payload["jti"], payload["exp"])
    if session is None:  # токен до шага 10.26 — без сеанса: заводим
        session = await account.start(db, user.id, ip, agent)
    else:
        await account.touch(db, session, ip, agent)
    return _issue_tokens(user.id, session.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: LogoutRequest | None = None, access_payload: dict = Depends(get_current_token_payload),
                 db: AsyncSession = Depends(get_db)):
    await blacklist.revoke(access_payload["jti"], access_payload["exp"])
    session = await account.get(db, access_payload.get("sid"))
    if session is not None and session.ended_at is None and str(session.user_id) == access_payload["sub"]:
        session.ended_at, session.end_reason = utcnow(), LOGOUT
        await db.commit()
    if data and data.refresh_token:
        try:
            p = decode_token(data.refresh_token, expected_type=REFRESH)
            if p["sub"] == access_payload["sub"]:
                await blacklist.revoke(p["jti"], p["exp"])
        except InvalidTokenError:
            pass  # невалидный refresh при выходе — не ошибка


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserResponse)
async def update_me(data: ProfileUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Имя, фамилия, телефон (шаг 10.26). Прежнее одно поле full_name делится на имя и фамилию. E-mail меняется после
    подключения почты (шаг 8.3a): новый адрес нужно подтвердить."""
    fields = data.model_fields_set
    if "full_name" in fields and not {"first_name", "last_name"} & fields:
        user.first_name, user.last_name = account.split_name(data.full_name)
    if "first_name" in fields:
        user.first_name = (data.first_name or "").strip() or None
    if "last_name" in fields:
        user.last_name = (data.last_name or "").strip() or None
    if "phone" in fields:
        try:
            user.phone = account.clean_phone(data.phone)
        except account.AccountError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if fields & {"full_name", "first_name", "last_name"}:
        user.full_name = account.full_name(user.first_name, user.last_name)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange,
    payload: dict = Depends(get_current_token_payload),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Смена пароля. Все сессии пользователя, включая текущую, завершаются — войти заново."""
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Текущий пароль указан неверно")
    if verify_password(data.new_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Новый пароль совпадает с текущим")
    user.hashed_password = hash_password(data.new_password)
    user.tokens_valid_after = utcnow()
    await db.commit()
    await account.end_all(db, user.id, PASSWORD)
    await blacklist.revoke(payload["jti"], payload["exp"])


# ── сеансы (шаг 10.26) ──
def _sid(payload: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(payload["sid"]) if payload.get("sid") else None
    except ValueError:
        return None


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(payload: dict = Depends(get_current_token_payload), user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Активные сеансы аккаунта: устройство, адрес, вход, последняя активность; этот — первым."""
    current = _sid(payload)
    rows = await account.active(db, user.id)
    rows.sort(key=lambda s: s.id != current)
    return [SessionOut(id=s.id, device=account.device_label(s.user_agent), ip=s.ip, created_at=utc(s.created_at),
                       last_seen_at=utc(s.last_seen_at), current=s.id == current) for s in rows]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(session_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Завершить сеанс: на том устройстве нужно войти снова (этот — тоже выход)."""
    try:
        await account.end(db, user.id, session_id, ENDED)
    except account.AccountError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/sessions/end-others")
async def end_other_sessions(payload: dict = Depends(get_current_token_payload), user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)) -> dict:
    """Завершить все сеансы, кроме этого."""
    return {"ended": await account.end_all(db, user.id, ENDED_ALL, keep=_sid(payload))}


# ── удаление аккаунта (шаг 10.26) ──
@router.post("/account/delete", response_model=UserResponse)
async def request_deletion(data: PasswordConfirm, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Удалить аккаунт: через 30 дней удалятся аккаунт и данные всех его магазинов; до этого всё работает и удаление
    можно отменить."""
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пароль указан неверно")
    return await account.request_deletion(db, user)


@router.post("/account/cancel-deletion", response_model=UserResponse)
async def cancel_deletion(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await account.cancel_deletion(db, user)
