"""«Мягкий» разбор значений из ответов Ozon: нет поля или мусор → None, а не исключение."""
from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any, q: Decimal) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(q)
    except (InvalidOperation, ValueError):
        return None


def to_id(value: Any) -> int | None:
    """Идентификатор Ozon (product_id, sku, warehouse_id): 0 и пусто — нет идентификатора."""
    try:
        return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def to_count(value: Any) -> int:
    """Количество штук: нет значения → 0, отрицательное → 0."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def to_str(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] or None


def list_items(data: Any, key: str = "items") -> list[dict]:
    """Список объектов из ответа: {"items": [...]} или {"result": {"items": [...]}}."""
    if not isinstance(data, dict):
        return []
    items = data.get(key)
    if items is None and isinstance(data.get("result"), dict):
        items = data["result"].get(key)
    return [i for i in items or [] if isinstance(i, dict)]
