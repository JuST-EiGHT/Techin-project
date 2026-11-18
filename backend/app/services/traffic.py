"""«Внешний трафик» (шаг 11.1): префикс UTM_CAMPAIGN магазина, поиск товаров для конструктора, сборка ссылок, выгрузка
и QR-код. Ссылки здесь не сохраняются — история ссылок появится в шаге 11.2."""
import csv
import io
import uuid

import segno
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import traffic_links as tl
from app.models.product import Product
from app.models.user_settings import UserSettings
from app.schemas.traffic import BuildRequest, BuildResult, BuildRow, ProductOption, ProductRef
from app.services import user_settings as settings_svc
from app.services.xlsx_export import Col, table_xlsx


async def get_prefix(db: AsyncSession, store_id: uuid.UUID) -> str | None:
    return (await settings_svc.get_settings(db, store_id)).traffic_utm_prefix


async def set_prefix(db: AsyncSession, store_id: uuid.UUID, text: str | None) -> str | None:
    """Пусто — префикс убран; иначе — проверен (LinkError с понятным текстом)."""
    value = tl.check_prefix(text) if (text or "").strip() else None
    row = await db.get(UserSettings, store_id)
    if row is None:
        row = settings_svc.defaults(store_id)
        db.add(row)
    row.traffic_utm_prefix = value
    await db.commit()
    return value


async def search_products(db: AsyncSession, store_id: uuid.UUID, text: str | None, limit: int) -> list[ProductOption]:
    """Товары магазина для конструктора: по артикулу, названию, SKU или штрихкоду (без учёта регистра и «ё»); в продаже —
    первыми. Фильтр — в Python: товаров у магазина сотни, а сравнение кириллицы без регистра в SQLite не работает."""
    found = (await db.scalars(
        select(Product).where(Product.user_id == store_id).order_by(Product.is_archived, Product.offer_id))).all()
    s = _fold(text)
    if s:
        found = [p for p in found if any(s in _fold(v) for v in (p.offer_id, p.name, p.barcode, p.ozon_sku))]
    return [_option(p) for p in found[:limit]]


def _fold(v) -> str:
    return "" if v is None else " ".join(str(v).split()).lower().replace("ё", "е")


def _option(p: Product) -> ProductOption:
    return ProductOption(id=p.id, offer_id=p.offer_id, name=p.name, sku=p.ozon_sku, archived=p.is_archived)


async def build(db: AsyncSession, store_id: uuid.UUID, data: BuildRequest) -> BuildResult:
    """Ссылки с метками: сначала выбранные товары (в порядке выбора), потом ссылки из списка. Ошибки меток (нет префикса,
    названия кампании, канала) — LinkError на весь запрос; ошибки ссылок — у своей строки."""
    labels = tl.make_labels(
        prefix=await get_prefix(db, store_id), campaign=data.campaign, channel=data.channel, source=data.source,
        medium=data.medium, term=data.term, content=data.content,
    )
    products = await _products(db, store_id, data.product_ids)
    lines = tl.split_lines(data.urls)
    if not products and not lines:
        raise tl.LinkError("Выберите товары или вставьте ссылки — по одной в строке")

    items: list[tuple[int, str, str | None]] = []
    pre_errors: dict[int, str] = {}
    for i, p in enumerate(products):
        if p.ozon_sku:
            items.append((i, tl.product_url(p.ozon_sku), str(p.id)))
        else:
            pre_errors[i] = "У товара нет SKU Ozon — обновите товары (кнопка «Синхронизация»)"
            items.append((i, "", str(p.id)))
    offset = len(products)
    items += [(offset + i, text, None) for i, (_, text) in enumerate(lines)]
    built = tl.build_rows(items, labels)

    by_id = {str(p.id): p for p in products}
    rows: list[BuildRow] = []
    for r in built:
        if r.line < offset:
            p = by_id[r.product_id]
            rows.append(BuildRow(
                kind="product", line=None, input=f"{p.offer_id} — {p.name or 'без названия'}",
                url=None if r.line in pre_errors else r.url, section=r.section,
                error=pre_errors.get(r.line) or r.error,
                product=ProductRef(id=p.id, offer_id=p.offer_id, name=p.name, sku=p.ozon_sku),
            ))
        else:
            rows.append(BuildRow(kind="url", line=lines[r.line - offset][0], input=r.input, url=r.url,
                                 section=r.section, error=r.error))
    ok = sum(1 for r in rows if r.url)
    return BuildResult(
        campaign=labels.campaign, source=labels.source, medium=labels.medium, term=labels.term, content=labels.content,
        rows=rows, ok=ok, errors=len(rows) - ok, warnings=[],
    )


async def _products(db: AsyncSession, store_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Product]:
    """Товары магазина в порядке выбора; чужие и несуществующие id пропускаются, повторы — один раз."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = {p.id: p for p in (await db.scalars(
        select(Product).where(Product.user_id == store_id, Product.id.in_(wanted)))).all()}
    return [found[i] for i in wanted if i in found]


# ---------- выгрузка ----------

_COLUMNS = [
    Col("Что размечено", 14), Col("Артикул", 18), Col("Название или исходная ссылка", 48), Col("Раздел Ozon", 18),
    Col("Ссылка с метками", 90), Col("utm_source", 14), Col("utm_medium", 14), Col("utm_campaign", 34),
    Col("utm_term", 16), Col("utm_content", 16),
]


def _table(result: BuildResult) -> list[list[str]]:
    out = []
    for r in result.rows:
        if not r.url:
            continue
        kind = "Товар" if r.kind == "product" else "Ссылка"
        offer = r.product.offer_id if r.product else ""
        what = (r.product.name or "") if r.product else r.input
        out.append([kind, offer, what, tl.SECTIONS.get(r.section or "", ""), r.url, result.source, result.medium,
                    result.campaign, result.term, result.content])
    return out


def to_xlsx(result: BuildResult) -> bytes:
    notes = [
        "Ссылки с UTM-метками для внешнего трафика Ozon",
        f"Кампания (utm_campaign): {result.campaign}. Ozon узнаёт кампанию по префиксу в начале метки — "
        "не меняйте её вручную, иначе переходы не попадут в статистику.",
        "Статистика — в кабинете Ozon: Продвижение → Внешний трафик; отчёты оттуда можно загрузить в сервис.",
        *result.warnings,
    ]
    return table_xlsx("Ссылки", _COLUMNS, _table(result), notes=notes)


def to_csv(result: BuildResult) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([c.title for c in _COLUMNS])
    w.writerows(_table(result))
    return buf.getvalue().encode("utf-8-sig")  # BOM — Excel открывает кириллицу без «кракозябр»


def qr(url: str, kind: str) -> bytes:
    """QR-код ссылки Ozon (проверяется, как в конструкторе): svg — для экрана и печати, png — картинкой."""
    tl.parse_link(url)
    code = segno.make(url, error="m", micro=False)
    buf = io.BytesIO()
    if kind == "svg":
        code.save(buf, kind="svg", scale=4, border=2, xmldecl=False, light="white")  # белый фон — сканируется и в тёмной теме
    else:
        code.save(buf, kind="png", scale=8, border=2, light="white")
    return buf.getvalue()
