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
    "report_id": "Отчёт",
    "report_type": "Тип отчёта",
    "on_conflict": "Действие при совпадении периода",
    "manual_total": "Сумма от Ozon",
    "vat_multiplier": "Коэффициент НДС",
    "tax_rate": "Ставка налога",
    "tax_base": "Налог считается",
    "theme": "Тема",
    "current_password": "Текущий пароль",
    "new_password": "Новый пароль",
    "cost_price": "Себестоимость",
    "note": "Комментарий",
    "effective_from": "Действует с",
    "search": "Поиск",
    "type": "Тип операции",
    "missing": "Без себестоимости",
    "dry_run": "Предпросмотр",
    "legal_form": "Кто вы",
    "regime": "Система налогообложения",
    "rate_pct": "Ставка налога",
    "custom_base": "Налог считается",
    "vat_mode": "НДС",
    "vat_rate_pct": "Ставка НДС",
    "supplier_vat_pct": "НДС в цене поставщика",
    "contributions_year": "Страховые взносы за год",
    "has_employees": "Есть сотрудники",
    "patent_cost_year": "Стоимость патента за год",
    "on": "Дата",
    "format": "Формат файла",
    "rows": "Какие строки",
    "offer_id": "Артикул",
    "source": "Источник данных",
    "supplier_payment": "Откуда товар",
    "supplier_deferral_days": "Отсрочка оплаты поставщику",
    "ozon_account_id": "Куда приходят выплаты Ozon",
    "threshold": "Порог остатка",
    "horizon_days": "Горизонт прогноза",
    "accounts": "Счета",
    "ozon_account": "Куда приходят выплаты Ozon",
    "categories": "Статьи",
    "codes": "Статьи",
    "name": "Название",
    "kind": "Тип",
    "balance": "Остаток",
    "as_of": "Дата остатка",
    "archived": "Архив",
    "amount": "Сумма",
    "day": "Дата",
    "account_id": "Счёт",
    "to_account_id": "Счёт, на который переводите",
    "category_id": "Статья",
    "counterparty": "Контрагент",
    "comment": "Описание",
    "search": "Поиск",
    "sort": "Сортировка",
    "order": "Порядок",
    "mode": "График выплат",
    "weekday": "День недели",
    "lag_weeks": "За какую неделю продаж",
    "fee_pct": "Комиссия за гибкий график",
    "horizon_weeks": "Горизонт прогноза",
    "price_ad_pct": "Реклама",
    "price_target_margin_pct": "Целевая маржа",
    "ad_pct": "Реклама",
    "target_margin_pct": "Целевая маржа",
    "commission_pct": "Комиссия Ozon",
    "acquiring_pct": "Эквайринг",
    "logistics": "Логистика на 1 шт.",
    "storage": "Хранение на 1 шт.",
    "other": "Прочие расходы на 1 шт.",
    "cost": "Себестоимость",
    "price": "Цена",
    "promo_price": "Цена в акции",
    "filters": "Фильтры",
    "stock_critical_days": "Порог «Критично»",
    "stock_low_days": "Порог «Мало»",
    "stock_excess_days": "Порог «Избыток»",
    "total": "Вывезти всего",
    "keep_idle": "Оставлять у товара без продаж",
    "keep_selling": "Оставлять у продающегося",
    "excluded": "Исключённые кластеры",
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
    if t in ("decimal_parsing", "decimal_type", "float_parsing", "float_type"):
        return f"{name}: нужно число"
    if t == "decimal_max_places":
        return f"{name}: не больше {err.get('ctx', {}).get('decimal_places')} знаков после запятой"
    if t in ("less_than", "greater_than"):
        ctx = err.get("ctx", {})
        return f"{name}: {'меньше' if t == 'less_than' else 'больше'} {ctx.get('lt', ctx.get('gt'))}"
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
