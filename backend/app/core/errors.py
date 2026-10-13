"""Единый формат ошибок: {"detail": "...", "code": <status>}.

Ошибки валидации Pydantic (по умолчанию 422 со сложной структурой)
превращаются в 400 с понятным списком полей.
"""
import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

_FIELD_NAMES = {
    "email": "Email",
    "password": "Пароль",
    "full_name": "Имя",
    "refresh_token": "Refresh-токен",
    "label": "Название",
    "client_id": "Client-Id",
    "api_key": "Api-Key",
    "expires_at": "Срок действия",
    "cred_id": "Ключ",
    "warehouse_id": "Склад",
    "product_id": "Товар",
    "status": "Статус",
    "date_from": "Дата начала",
    "date_to": "Дата окончания",
    "type_id": "Тип начисления",
    "posting_number": "Номер отправления",
    "limit": "Лимит",
    "offset": "Смещение",
    "promotion_id": "Акция",
    "participating": "Участие",
    "include_finished": "Закончившиеся акции",
    "file": "Файл",
}


def _humanize(err: dict) -> str:
    field = next((str(p) for p in reversed(err.get("loc", [])) if p not in ("body", "query", "path")), None)
    name = _FIELD_NAMES.get(field, field) if field else "Запрос"
    t = err.get("type", "")
    if t == "missing":
        return f"{name}: обязательное поле"
    if t == "string_too_short":
        return f"{name}: минимум {err.get('ctx', {}).get('min_length')} символов"
    if t == "string_too_long":
        return f"{name}: максимум {err.get('ctx', {}).get('max_length')} символов"
    if "email" in t or field == "email":
        return f"{name}: некорректный адрес"
    if t in ("literal_error", "enum"):
        return f"{name}: недопустимое значение"
    if t in ("date_from_datetime_parsing", "date_parsing", "date_type"):
        return f"{name}: дата в формате ГГГГ-ММ-ДД"
    if t in ("bool_parsing", "bool_type"):
        return f"{name}: нужно true или false"
    if t in ("int_parsing", "int_type"):
        return f"{name}: нужно целое число"
    if t in ("less_than_equal", "greater_than_equal"):
        ctx = err.get("ctx", {})
        bound = ctx.get("le", ctx.get("ge"))
        return f"{name}: {'не больше' if t == 'less_than_equal' else 'не меньше'} {bound}"
    if t == "uuid_parsing":
        return f"{name}: некорректный идентификатор"
    if t == "value_error" and "error" in err.get("ctx", {}):
        return f"{name}: {err['ctx']['error']}"  # текст из наших field_validator
    return f"{name}: {err.get('msg')}"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail, "code": exc.status_code}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError):
        errors = [_humanize(e) for e in exc.errors()]
        return JSONResponse(
            {"detail": "; ".join(errors), "errors": errors, "code": 400}, status_code=status.HTTP_400_BAD_REQUEST
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Необработанная ошибка: %s", exc)
        return JSONResponse({"detail": "Внутренняя ошибка сервера", "code": 500}, status_code=500)
