"""«Все операции» — единый журнал денег (шаг 10.19).

Факт — операции (3.8, 3.11, 3.12) как в `planning.list_operations`; план — платежи календаря (3.9), ещё не оплаченные:
плановые и просроченные. Оплаченный платёж в журнале — его фактом (у факта видно, какой платёж он закрыл); пропущенные
и выключенные — только в «Календаре». Плановая → фактическая — «Оплачено» календаря (`plan_calendar.mark_paid`), здесь
ничего нового не хранится: журнал — представление, исходные записи прежние (правило 12).

Массовое изменение — новая версия каждой выбранной операции (как правка по одной); всё или ничего: строку, которую
изменить нельзя, называем, и ничего не меняется.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import payment_calendar as cal
from app.engine.anomaly_text import rub
from app.models.planning import MoneyOperation
from app.services import categorization
from app.services import plan_calendar
from app.services import planning as planning_svc
from app.services.planning import PlanningError
from app.services.xlsx_export import Col, table_xlsx

ZERO = Decimal(0)
CENT = Decimal("0.01")
PLAN_WINDOW_DAYS = 400  # как у календаря: длиннее — только факт
STATUSES = ("fact", "planned", "overdue")
PLAN_SOURCE = {"manual": "plan", "supplier": "supplier", "tax": "tax", "ozon": "ozon_forecast"}
STATUS_TEXT = {"fact": "Факт", "planned": "План", "overdue": "Просрочен"}
TYPE_TEXT = {"income": "Поступление", "expense": "Платёж", "transfer": "Перевод"}
SOURCE_TEXT = {
    "manual": "Вручную", "import": "Из файла", "ozon_payout": "Выплата Ozon", "bank": "Банк",
    "plan": "Плановый платёж", "supplier": "Поставщик (план)", "tax": "Налог (план)", "ozon_forecast": "Ozon (прогноз)",
}
SORTS = ("day", "amount", "category")


def _fact_type(op: dict) -> str:
    if op["transfer"]:
        return "transfer"
    return "income" if op["amount"] > 0 else "expense"


async def journal(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, *, status: str | None = None,
    type_: str | None = None, account_id: uuid.UUID | None = None, category_id: uuid.UUID | None = None,
    uncategorized: bool = False, source: str | None = None, search: str | None = None, sort: str = "day",
    desc: bool = True, limit: int = 100, offset: int = 0,
) -> dict:
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    accounts = {a.id: a for a in await planning_svc._accounts(db, user_id)}
    wanted = await planning_svc.subcategory_ids(db, user_id, category_id) if category_id else None

    def cat_refs(cid) -> tuple[dict | None, dict | None]:
        c = cats.get(cid)
        if c is None:
            return None, None
        parent = cats.get(c.parent_id) if c.parent_id else None
        return ({"id": c.id, "name": c.name, "kind": c.kind, "parent_id": c.parent_id},
                {"id": parent.id, "name": parent.name} if parent else None)

    facts = await planning_svc.list_operations(db, user_id, date_from, date_to, account_id, category_id, search,
                                               limit=10 ** 9, offset=0, uncategorized=uncategorized)
    rows: list[dict] = []
    for op in facts["items"]:
        category, parent = cat_refs(op["category"]["id"] if op["category"] else None)
        rows.append({
            "id": f"op:{op['op_id']}", "status": "fact", "type": _fact_type(op), "day": op["day"],
            "amount": op["amount"], "category": category, "parent": parent, "account": op["account"],
            "counterparty": op["counterparty"], "comment": op["comment"], "source": op["source"], "operation": op,
            "plan": None,
        })

    plan_note = None
    settings = await planning_svc.get_settings(db, user_id)
    if settings is None or not settings.enabled:
        plan_note = "Плановые платежи появятся, когда включено «Планирование»"
    elif (date_to - date_from).days > PLAN_WINDOW_DAYS:
        plan_note = f"Плановые платежи — при периоде до {PLAN_WINDOW_DAYS} дней"
    else:
        snap = await plan_calendar.snapshot(db, user_id, date_from, date_to)
        today = snap.ctx.today
        closed = {i.fact.op_id: i for i in snap.items if i.fact is not None and i.fact.op_id}
        for row in rows:  # факт, закрывший платёж календаря
            item = closed.get(str(row["operation"]["op_id"]))
            if item is not None:
                row["plan"] = _plan_ref(item)
        needle = (search or "").strip().lower()
        by_code = {c.code: c.id for c in cats.values() if c.code and c.archived_at is None}
        for item in snap.visible:
            st = item.status(today)
            if st not in ("planned", "overdue"):
                continue
            meta = item.meta
            # статья и счёт — те, с которыми «Оплачено» запишет операцию
            cid = meta.get("category_id") or by_code.get(plan_calendar.TAX_CATEGORY.get(item.source))
            acc = accounts.get(meta.get("account_id") or (settings.ozon_account_id if item.source == "ozon" else None))
            if account_id is not None and (acc is None or acc.id != account_id):
                continue
            if wanted is not None and cid not in wanted:
                continue
            if uncategorized and cid is not None:
                continue
            text = " ".join(filter(None, (item.title, item.counterparty, meta.get("comment")))).lower()
            if needle and needle not in text:
                continue
            category, parent = cat_refs(cid)
            rows.append({
                "id": f"plan:{item.key}", "status": st, "type": item.kind, "day": item.day,
                "amount": item.amount.quantize(CENT) if item.amount is not None else None,
                "category": category, "parent": parent,
                "account": {"id": acc.id, "name": acc.name} if acc else None,
                "counterparty": item.counterparty, "comment": meta.get("comment") or item.title,
                "source": PLAN_SOURCE.get(item.source, "plan"), "operation": None, "plan": _plan_ref(item),
            })

    if type_:
        rows = [r for r in rows if r["type"] == type_]
    if source:
        rows = [r for r in rows if r["source"] == source]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    if status:
        rows = [r for r in rows if r["status"] == status]

    def total(items: list[dict]) -> dict:
        inc = sum((r["amount"] for r in items if r["type"] == "income" and r["amount"] is not None), ZERO)
        exp = sum((r["amount"] for r in items if r["type"] == "expense" and r["amount"] is not None), ZERO)
        return {"income": inc.quantize(CENT), "expense": exp.quantize(CENT), "net": (inc + exp).quantize(CENT)}

    totals = {"fact": total([r for r in rows if r["status"] == "fact"]),
              "plan": total([r for r in rows if r["status"] != "fact"])}
    rows = _sorted(rows, sort, desc)
    return {
        "date_from": date_from, "date_to": date_to, "total": len(rows), "limit": limit, "offset": offset,
        "items": rows[offset:offset + limit], "totals": totals, "counts": counts, "plan_note": plan_note,
    }


def _plan_ref(item: cal.Item) -> dict:
    plan_id = item.meta.get("plan_id")
    return {"key": item.key, "title": item.title, "plan_id": plan_id, "estimate": item.estimate,
            "link": item.meta.get("link")}


def _sorted(rows: list[dict], sort: str, desc: bool) -> list[dict]:
    """Без суммы и без статьи — в конце при любом направлении; при равенстве — новые сверху, факт раньше плана."""
    rows = sorted(rows, key=lambda r: (r["status"] != "fact",))
    rows = sorted(rows, key=lambda r: r["day"], reverse=True)
    if sort == "amount":
        known = sorted([r for r in rows if r["amount"] is not None], key=lambda r: abs(r["amount"]), reverse=desc)
        return known + [r for r in rows if r["amount"] is None]
    if sort == "category":
        def name(r: dict) -> str:
            c, p = r["category"], r["parent"]
            return f"{p['name']} / {c['name']}".lower() if p else c["name"].lower()
        known = sorted([r for r in rows if r["category"]], key=name, reverse=desc)
        return known + [r for r in rows if not r["category"]]
    return rows if desc else list(reversed(rows))


# ---------- экспорт ----------

COLUMNS = [
    Col("Дата", 12, "date"), Col("Статус", 12), Col("Тип", 14), Col("Сумма, ₽", 14, "money"), Col("Статья", 24),
    Col("Подстатья", 24), Col("Счёт", 20), Col("Контрагент", 24), Col("Описание", 40), Col("Источник", 18),
]


def to_xlsx(data: dict) -> bytes:
    rows = []
    for r in data["items"]:
        c, p = r["category"], r["parent"]
        rows.append([
            r["day"], STATUS_TEXT[r["status"]], TYPE_TEXT[r["type"]], r["amount"],
            (p["name"] if p else c["name"]) if c else ("Перевод" if r["type"] == "transfer" else "Без статьи"),
            c["name"] if p else None, r["account"]["name"] if r["account"] else None, r["counterparty"], r["comment"],
            SOURCE_TEXT[r["source"]],
        ])
    f, pl = data["totals"]["fact"], data["totals"]["plan"]
    notes = [
        f"Операции {data['date_from']:%d.%m.%Y} — {data['date_to']:%d.%m.%Y}",
        "",
        "Факт — операции по счетам; план и просроченные — платежи «Календаря», ещё не оплаченные. Переводы между "
        "своими счетами — не поступление и не платёж, в итоги не входят.",
        f"Факт: поступления {f['income']:.2f} ₽, платежи {f['expense']:.2f} ₽, итого {f['net']:.2f} ₽.",
        f"План: поступления {pl['income']:.2f} ₽, платежи {pl['expense']:.2f} ₽ (суммы-оценки — как есть).",
    ]
    if data.get("plan_note"):
        notes.append(data["plan_note"])
    return table_xlsx("Операции", COLUMNS, rows, notes_title="Пояснения", notes=notes)


# ---------- массовое изменение ----------


def _label(op: MoneyOperation) -> str:
    return f"Операция {op.day:%d.%m.%Y} на {'+' if op.amount > 0 else ''}{rub(op.amount)}"


async def bulk_update(db: AsyncSession, user_id: uuid.UUID, op_ids: list[uuid.UUID], changes: dict) -> tuple[int, int]:
    """changes — только меняемые поля: category_id (None — убрать статью), account_id, counterparty, comment.
    Возвращает (изменено, уже было так)."""
    if not changes:
        raise PlanningError("Выберите, что изменить")
    ids = list(dict.fromkeys(op_ids))
    rows = list((await db.execute(planning_svc._current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id.in_(ids))))).scalars())
    if len(rows) != len(ids):
        raise planning_svc.NotFound("Часть операций не найдена — обновите страницу")
    if "category_id" in changes and changes["category_id"] is not None:
        cat = await planning_svc._category(db, user_id, changes["category_id"])
        if cat.kind == "transfer" or cat.archived_at is not None:
            raise PlanningError("Выберите действующую статью поступлений или расходов")
        for op in rows:
            if op.transfer_id is None and cat.kind != ("income" if op.amount > 0 else "expense"):
                word = "поступлений" if op.amount > 0 else "расходов"
                raise PlanningError(f"Среди выбранных есть {'поступления' if op.amount > 0 else 'платежи'} — для них "
                                    f"нужна статья {word}. Выберите только поступления или только платежи")
    changed = 0
    for op in sorted(rows, key=lambda r: (r.day, r.created_at)):
        try:
            _, did = await planning_svc.update_operation(db, user_id, op.op_id, dict(changes))
        except PlanningError as exc:
            text = str(exc)
            raise PlanningError(f"{_label(op)}: {text[:1].lower()}{text[1:]}") from exc
        changed += did
        await db.flush()
    if changes.get("category_id", 0) is None:  # статью убрали — пусть разнесут правила, если подходят
        await categorization.apply_rules(db, user_id, ids)
    return changed, len(rows) - changed
