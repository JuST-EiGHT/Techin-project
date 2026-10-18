import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import user as crud_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import (
    LogoutRequest,
    PasswordChange,
    ProfileUpdate,
    RefreshRequest,
    RegistrationInfo,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.auth import (
    REFRESH,
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
from app.services import login_limit, registration
from app.services.audit import client_info
from app.services.token_blacklist import blacklist

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user_id: uuid.UUID) -> Token:
    return Token(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
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
    return _issue_tokens(user.id)


@router.post("/refresh", response_model=Token)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
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

    # Ротация: старый refresh больше не работает
    await blacklist.revoke(payload["jti"], payload["exp"])
    return _issue_tokens(user.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: LogoutRequest | None = None, access_payload: dict = Depends(get_current_token_payload)):
    await blacklist.revoke(access_payload["jti"], access_payload["exp"])
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
    if "full_name" in data.model_fields_set:
        user.full_name = (data.full_name or "").strip() or None
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
    await blacklist.revoke(payload["jti"], payload["exp"])
