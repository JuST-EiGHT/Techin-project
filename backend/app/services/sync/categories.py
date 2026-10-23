"""Названия категорий Ozon (шаг 10.3) — для фильтра «категория» в аналитике и товарах.

`/v1/description-category/tree` (сверено с docs/ozon/seller_api_2026-10.txt): дерево `result[]` — у узла
`description_category_id`, `category_name`, `children`; на последнем уровне — типы товаров (`type_id`, `type_name`), их
пропускаем: у товара хранится `description_category_id` (services/sync/products.py). Дерево — весь каталог Ozon, поэтому
сохраняем только категории товаров магазина и их родителей (для пути «Автотовары / Подшипники»). Загружаем после
синхронизации товаров, если у товара появилась неизвестная категория или справочник старше REFRESH_AFTER.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ozon.client import OzonClient
from app.models.ozon_category import OzonCategory
from app.models.product import Product
from app.services.sync.parsing import to_id, to_str

log = logging.getLogger(__name__)

TREE_PATH = "/v1/description-category/tree"
REFRESH_AFTER = timedelta(days=30)  # Ozon переименовывает категории редко
MAX_NODES = 200_000  # защита от неожиданно огромного ответа


def walk(tree: Any) -> dict[int, tuple[str, int | None, str]]:
    """Категория → (название, родитель, путь от корня). Узлы без id или названия (типы товаров) — пропускаем."""
    out: dict[int, tuple[str, int | None, str]] = {}
    stack = [(node, None, "") for node in reversed(tree if isinstance(tree, list) else [])]
    while stack and len(out) < MAX_NODES:
        node, parent, path = stack.pop()
        if not isinstance(node, dict):
            continue
        cid, name = to_id(node.get("description_category_id")), to_str(node.get("category_name"), 255)
        if cid is None or name is None:
            continue
        full = f"{path} / {name}" if path else name
        out[cid] = (name, parent, full[:1000])
        children = node.get("children")
        stack.extend((child, cid, full) for child in reversed(children if isinstance(children, list) else []))
    return out


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite в тестах отдаёт без пояса


async def needed(db: AsyncSession, user_id: uuid.UUID) -> set[int]:
    res = await db.execute(
        select(Product.category_id).where(Product.user_id == user_id, Product.category_id.is_not(None)).distinct()
    )
    return set(res.scalars())


async def refresh(db: AsyncSession, user_id: uuid.UUID, client: OzonClient, force: bool = False) -> dict:
    """Загрузить названия категорий товаров магазина, если какой-то нет или справочник устарел. Ошибки Ozon — наружу
    (вызывающий решает, валить ли синхронизацию)."""
    ids = await needed(db, user_id)
    if not ids:
        return {"categories": 0}
    now = datetime.now(timezone.utc)
    known = {c.id: c for c in (await db.execute(select(OzonCategory).where(OzonCategory.id.in_(ids)))).scalars()}
    fresh = all(_utc(c.updated_at) > now - REFRESH_AFTER for c in known.values())
    if not force and len(known) == len(ids) and fresh:
        return {"categories": len(known), "categories_loaded": False}

    data = await client.request("POST", TREE_PATH, {"language": "DEFAULT"})
    tree = walk(data.get("result") if isinstance(data, dict) else None)
    keep: set[int] = set()
    for cid in ids:  # категория товара и все её родители
        while cid is not None and cid in tree and cid not in keep:
            keep.add(cid)
            cid = tree[cid][1]
    existing = {c.id: c for c in (await db.execute(select(OzonCategory).where(OzonCategory.id.in_(keep)))).scalars()}
    for cid in keep:
        name, parent, path = tree[cid]
        row = existing.get(cid)
        if row is None:
            db.add(OzonCategory(id=cid, name=name, path=path, parent_id=parent, updated_at=now))
        else:
            row.name, row.path, row.parent_id, row.updated_at = name, path, parent, now
    await db.commit()
    unknown = len(ids - keep)
    if unknown:
        log.info("Категории Ozon: %d категорий товаров нет в дереве Ozon (магазин %s)", unknown, user_id)
    return {"categories": len(ids & keep), "categories_unknown": unknown, "categories_loaded": True}
