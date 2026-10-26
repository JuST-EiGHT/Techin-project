"""Правила разнесения операций по статьям (шаг 3.12).

- Ручная разметка важнее правил: статью, поставленную пользователем (`category_by = user`), правила не трогают.
- Встроенные правила раньше пользовательских: маркетплейс по ИНН и перевод между своими счетами ставятся при загрузке
  выписки (`category_by = auto`); налоги по реквизитам казначейства — здесь же, при разнесении.
- Новое или изменённое правило пересчитывает только операции без ручной разметки; операция, которой статью поставило
  удалённое правило, остаётся без статьи (или получает статью следующего подошедшего правила). Изменение статьи —
  новая версия операции (правило 12).
- Правила работают для операций из банка (файл — шаг 3.11, API — 3.12), из загрузки файлом (3.8) и внесённых вручную
  без статьи. Выплаты Ozon и переводы между своими счетами правила не трогают.
- Журнал применения (шаг 10.20): каждый пересчёт — запись `rule_runs` (почему, сколько проверено и изменено, какие
  операции: было → стало, ошибки и предупреждения). Пересчёт всех операций — сначала проверка (`dry_run`), потом
  применение.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import categorize as eng
from app.models.bank import BankTransaction
from app.models.planning import RULE_SOURCES, CashCategory, CategorizationRule, MoneyOperation, RuleRun
from app.services import planning as planning_svc
from app.services.planning import PlanningError, _current

MAX_RULES = 300
DIRECTION_KIND = {"in": "income", "out": "expense"}
SOURCE_TEXT = {"bank": "из банка", "import": "из файла операций", "manual": "внесена вручную"}
TRIGGER_TEXT = {
    "rule_created": "Добавлено правило", "rule_changed": "Изменено правило", "rule_deleted": "Удалено правило",
    "reorder": "Изменён порядок правил", "manual": "Правила применены ко всем операциям",
    "categorize": "Статью убрали вручную — разнесли правила", "bank_file": "Загружена выписка",
    "bank_api": "Операции из банка по API",
}
RUN_DETAILS = 100  # операций в записи журнала — первые 100, всего — в `changed`
BUILTIN = [
    {"code": "marketplaces", "title": "Поступления от маркетплейсов",
     "when": "Плательщик — маркетплейс (Ozon) по ИНН из справочника", "category": "Поступления от маркетплейсов",
     "why": "Поступление от Ozon — это выплата Ozon: одна операция, сверка с данными Ozon"},
    {"code": "transfers", "title": "Переводы между своими счетами",
     "when": "Счёт контрагента — ваш другой счёт", "category": "Перевод между своими счетами",
     "why": "Не доход и не расход: деньги остаются у вас"},
    {"code": "taxes", "title": "Налоги и взносы",
     "when": "Получатель — Казначейство России (ЕНП, ИНН 7727406020, счета 03100643…)", "category": "Налоги и взносы",
     "why": "Платёж закрывает плановый налог с ближайшим сроком в календаре — прогноз не считает его дважды"},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- правила: список и изменение ----------


async def _rules(db: AsyncSession, user_id: uuid.UUID, deleted: bool = False) -> list[CategorizationRule]:
    q = select(CategorizationRule).where(CategorizationRule.user_id == user_id)
    if not deleted:
        q = q.where(CategorizationRule.deleted_at.is_(None))
    res = await db.execute(q.order_by(CategorizationRule.priority, CategorizationRule.created_at))
    return list(res.scalars())


def _engine_rule(r: CategorizationRule) -> eng.Rule:
    return eng.Rule(
        id=str(r.id), direction=r.direction, category_id=str(r.category_id), priority=r.priority,
        counterparty_inn=r.counterparty_inn, counterparty=r.counterparty, purpose=r.purpose,
        amount_min=None if r.amount_min is None else Decimal(r.amount_min),
        amount_max=None if r.amount_max is None else Decimal(r.amount_max), enabled=r.enabled,
        account_id=str(r.account_id) if r.account_id else None, source=r.source,
    )


@dataclass
class RuleIn:
    direction: str
    category_id: uuid.UUID
    counterparty_inn: str | None = None
    counterparty: str | None = None
    purpose: str | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    enabled: bool = True
    account_id: uuid.UUID | None = None
    source: str | None = None


async def _check(db: AsyncSession, user_id: uuid.UUID, data: RuleIn) -> RuleIn:
    if data.direction not in DIRECTION_KIND:
        raise PlanningError("Правило — для поступлений или для платежей")
    cat = await planning_svc._category(db, user_id, data.category_id)
    if cat.archived_at is not None:
        raise PlanningError(f"Статья «{cat.name}» в архиве — выберите другую")
    if cat.kind != DIRECTION_KIND[data.direction]:
        raise PlanningError("Для поступлений выберите статью поступлений, для платежей — статью расходов")
    inn = "".join(ch for ch in (data.counterparty_inn or "") if ch.isdigit()) or None
    if data.counterparty_inn and (inn is None or len(inn) not in (10, 12)):
        raise PlanningError("ИНН — 10 цифр у организации или 12 у ИП")
    data.counterparty_inn = inn
    data.counterparty = planning_svc._clean(data.counterparty, 200)
    data.purpose = planning_svc._clean(data.purpose, 500)
    for value in (data.amount_min, data.amount_max):
        if value is not None and value < 0:
            raise PlanningError("Сумма в правиле — больше нуля; поступление или платёж — выбирается отдельно")
    if data.amount_min is not None and data.amount_max is not None and data.amount_min > data.amount_max:
        raise PlanningError("Сумма «от» больше суммы «до»")
    if data.account_id is not None:
        await planning_svc._account(db, user_id, data.account_id, active=False)
    if data.source is not None and data.source not in RULE_SOURCES:
        raise PlanningError("Источник — из банка, из файла операций или вручную")
    if not eng.has_conditions(_probe(data)):
        raise PlanningError("Укажите хотя бы одно условие: ИНН, контрагента, слова в назначении, сумму, счёт или источник")
    return data


def _probe(data: RuleIn, rule_id: str = "") -> eng.Rule:
    return eng.Rule(rule_id, data.direction, str(data.category_id or ""), counterparty_inn=data.counterparty_inn,
                    counterparty=data.counterparty, purpose=data.purpose, amount_min=data.amount_min,
                    amount_max=data.amount_max, account_id=str(data.account_id) if data.account_id else None,
                    source=data.source)


async def create_rule(db: AsyncSession, user_id: uuid.UUID, data: RuleIn) -> CategorizationRule:
    data = await _check(db, user_id, data)
    rules = await _rules(db, user_id)
    if len(rules) >= MAX_RULES:
        raise PlanningError(f"Правил уже {MAX_RULES} — удалите ненужные")
    rule = CategorizationRule(
        id=uuid.uuid4(), user_id=user_id, priority=(max((r.priority for r in rules), default=0) + 10),
        direction=data.direction, counterparty_inn=data.counterparty_inn, counterparty=data.counterparty,
        purpose=data.purpose, amount_min=data.amount_min, amount_max=data.amount_max, category_id=data.category_id,
        enabled=data.enabled, account_id=data.account_id, source=data.source,
    )
    db.add(rule)
    await db.flush()
    return rule


async def _rule(db: AsyncSession, user_id: uuid.UUID, rule_id: uuid.UUID) -> CategorizationRule:
    rule = await db.get(CategorizationRule, rule_id)
    if rule is None or rule.user_id != user_id or rule.deleted_at is not None:
        raise planning_svc.NotFound("Правило не найдено")
    return rule


async def update_rule(db: AsyncSession, user_id: uuid.UUID, rule_id: uuid.UUID, changes: dict) -> CategorizationRule:
    rule = await _rule(db, user_id, rule_id)
    fields = ("direction", "category_id", "counterparty_inn", "counterparty", "purpose", "amount_min", "amount_max",
              "enabled", "account_id", "source")
    data = await _check(db, user_id, RuleIn(**{k: changes.get(k, getattr(rule, k)) for k in fields}))
    for k in fields:
        setattr(rule, k, getattr(data, k))
    rule.updated_at = _now()
    await db.flush()
    return rule


async def delete_rule(db: AsyncSession, user_id: uuid.UUID, rule_id: uuid.UUID) -> None:
    rule = await _rule(db, user_id, rule_id)
    rule.deleted_at = _now()
    await db.flush()


async def reorder(db: AsyncSession, user_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
    rules = {r.id: r for r in await _rules(db, user_id)}
    if set(ids) != set(rules) or len(ids) != len(rules):
        raise PlanningError("Порядок: передайте все правила по одному разу")
    for i, rule_id in enumerate(ids):
        rules[rule_id].priority = (i + 1) * 10
    await db.flush()


def rule_text(r: CategorizationRule, accounts: dict) -> str:
    """Условия одной строкой — для журнала: «Платёж · контрагент «Петров» · счёт «Карта»»."""
    parts = ["Поступление" if r.direction == "in" else "Платёж"]
    if r.counterparty_inn:
        parts.append(f"ИНН {r.counterparty_inn}")
    if r.counterparty:
        parts.append(f"контрагент «{r.counterparty}»")
    if r.purpose:
        parts.append(f"в назначении «{r.purpose}»")
    if r.amount_min is not None:
        parts.append(f"от {Decimal(r.amount_min):.2f} ₽")
    if r.amount_max is not None:
        parts.append(f"до {Decimal(r.amount_max):.2f} ₽")
    if r.account_id:
        parts.append(f"счёт «{accounts.get(r.account_id, '—')}»")
    if r.source:
        parts.append(SOURCE_TEXT[r.source])
    return " · ".join(parts)


async def rules_out(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Правила по порядку: условия, статья, сколько операций разнесено (`operations`) и подходит под условия
    (`matches`), предупреждение (`problem`: статья в архиве, правило не срабатывает — операции забирает правило выше)."""
    rules = await _rules(db, user_id)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    accounts = {a.id: a for a in await planning_svc._accounts(db, user_id)}
    counts = dict((await db.execute(_current(select(MoneyOperation.rule_id, func.count()).where(
        MoneyOperation.user_id == user_id, MoneyOperation.category_by == "rule",
        MoneyOperation.rule_id.in_([r.id for r in rules]),
    )).group_by(MoneyOperation.rule_id))).all()) if rules else {}
    stats, problems = await _diagnose(db, user_id, rules, cats, accounts) if rules else ({}, {})
    return [
        {"id": r.id, "number": i + 1, "priority": r.priority, "direction": r.direction,
         "counterparty_inn": r.counterparty_inn, "counterparty": r.counterparty, "purpose": r.purpose,
         "amount_min": r.amount_min, "amount_max": r.amount_max,
         "account": {"id": r.account_id, "name": accounts[r.account_id].name} if r.account_id in accounts else None,
         "source": r.source,
         "category": {"id": r.category_id, "name": cats[r.category_id].name, "kind": cats[r.category_id].kind}
         if r.category_id in cats else None,
         "enabled": r.enabled, "operations": counts.get(r.id, 0),
         "matches": stats.get(str(r.id), {}).get("fits", 0), "problem": problems.get(r.id)}
        for i, r in enumerate(rules)
    ]


async def _diagnose(db: AsyncSession, user_id: uuid.UUID, rules: list[CategorizationRule], cats: dict,
                    accounts: dict, ops: list[MoneyOperation] | None = None) -> tuple[dict, dict]:
    """Покрытие правил по операциям без ручной разметки и предупреждения по правилам: {rule_id: текст}."""
    if ops is None:
        ops = await _eligible_ops(db, user_id)
    facts = await _facts(db, user_id, ops)
    pk = await picker(db, user_id)
    number = {r.id: i + 1 for i, r in enumerate(rules)}

    def winner(f: eng.Facts) -> str | None:
        hit = pk.pick(f)
        if hit is None:
            return None
        return str(hit[2]) if hit[2] else "taxes"

    stats = eng.coverage([_engine_rule(r) for r in rules if r.enabled], list(facts.values()), winner)
    problems: dict[uuid.UUID, str] = {}
    for r in rules:
        cat = cats.get(r.category_id)
        if r.enabled and cat is not None and cat.archived_at is not None:
            problems[r.id] = f"Статья «{cat.name}» в архиве — правило не применяется: выберите другую статью"
            continue
        acc = accounts.get(r.account_id) if r.account_id else None
        if r.enabled and acc is not None and acc.archived_at is not None:
            problems[r.id] = f"Счёт «{acc.name}» в архиве — новых операций по нему не будет"
            continue
        st = stats.get(str(r.id))
        if st and st["fits"] and not st["wins"]:
            top = st["taken_by"].most_common(1)[0][0]
            by = "встроенное правило «Налоги и взносы»" if top == "taxes" else f"правило №{number.get(uuid.UUID(top), '?')}"
            problems[r.id] = (f"Не срабатывает: все подходящие операции ({st['fits']}) забирает {by} — оно выше. "
                              "Поднимите это правило или уточните условия")
    return stats, problems


# ---------- разнесение ----------


async def _facts(db: AsyncSession, user_id: uuid.UUID, ops: list[MoneyOperation]) -> dict[uuid.UUID, eng.Facts]:
    """ИНН, счёт контрагента и назначение — из операции банка; у остальных назначение — описание операции."""
    bank_ids = [op.op_id for op in ops if op.source == "bank"]
    txs: dict[uuid.UUID, BankTransaction] = {}
    for i in range(0, len(bank_ids), 500):
        res = await db.execute(select(BankTransaction).where(
            BankTransaction.user_id == user_id, BankTransaction.op_id.in_(bank_ids[i:i + 500])))
        txs.update({t.op_id: t for t in res.scalars()})
    out = {}
    for op in ops:
        tx = txs.get(op.op_id)
        out[op.op_id] = eng.Facts(
            amount=Decimal(op.amount), counterparty=(tx.counterparty if tx and tx.counterparty else op.counterparty),
            inn=tx.counterparty_inn if tx else None, purpose=(tx.purpose if tx and tx.purpose else op.comment),
            counterparty_account=tx.counterparty_account if tx else None,
            account_id=str(op.account_id), source=op.source,
        )
    return out


async def tax_category(db: AsyncSession, user_id: uuid.UUID, create: bool = False) -> uuid.UUID | None:
    """Статья «Налоги и взносы» (код taxes); нет — добавим из шаблона, если create."""
    cats = {c.code: c for c in await planning_svc._categories(db, user_id) if c.code}
    if "taxes" not in cats or cats["taxes"].archived_at is not None:
        if not create:
            return None
        await planning_svc.add_template_categories(db, user_id, ["taxes"])
        await db.flush()
        cats = {c.code: c for c in await planning_svc._categories(db, user_id) if c.code}
    return cats["taxes"].id


class Picker:
    """Статья по правилам для новой операции или пересчёта: (статья, кто поставил, правило) или None — не подошло."""

    def __init__(self, rules: list[CategorizationRule], tax_id: uuid.UUID | None, active_cats: set[uuid.UUID]):
        self.rules = [_engine_rule(r) for r in rules if r.category_id in active_cats]
        self.tax_id = tax_id

    def pick(self, f: eng.Facts) -> tuple[uuid.UUID, str, uuid.UUID | None] | None:
        if self.tax_id is not None and eng.is_tax(f):
            return self.tax_id, "auto", None
        rule = eng.pick(self.rules, f)
        return (uuid.UUID(rule.category_id), "rule", uuid.UUID(rule.id)) if rule else None


async def picker(db: AsyncSession, user_id: uuid.UUID, facts: list[eng.Facts] | None = None) -> Picker:
    """Готовит правила; статью «Налоги и взносы» добавляет, только если среди операций есть платёж в бюджет."""
    need_tax = any(eng.is_tax(f) for f in facts or [])
    tax_id = await tax_category(db, user_id, create=need_tax)
    active = {c.id for c in await planning_svc._categories(db, user_id) if c.archived_at is None}
    return Picker(await _rules(db, user_id), tax_id, active)


def _eligible(op: MoneyOperation) -> bool:
    """Операция, которую можно разнести правилом: не выплата Ozon, не перевод, без ручной и встроенной разметки."""
    return (op.transfer_id is None and op.source != "ozon_payout" and op.deleted_at is None
            and op.superseded_at is None and op.category_by in (None, "rule"))


async def _eligible_ops(db: AsyncSession, user_id: uuid.UUID, op_ids: list[uuid.UUID] | None = None) -> list[MoneyOperation]:
    q = _current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.is_(None),
        MoneyOperation.source != "ozon_payout",
    ))
    if op_ids is not None:
        q = q.where(MoneyOperation.op_id.in_(op_ids))
    return [op for op in (await db.execute(q.order_by(MoneyOperation.day.desc()))).scalars() if _eligible(op)]


async def _changes(db: AsyncSession, user_id: uuid.UUID, ops: list[MoneyOperation]) -> list[tuple[MoneyOperation, tuple]]:
    """Что поменяют правила: (операция, (статья, кто поставил, правило)) — только где статья станет другой."""
    if not ops:
        return []
    facts = await _facts(db, user_id, ops)
    pk = await picker(db, user_id, list(facts.values()))
    out = []
    for op in ops:
        hit = pk.pick(facts[op.op_id])
        if hit is None:
            if op.category_by != "rule":
                continue  # статьи не было — и не появилось
            target = (None, None, None)  # правило удалено или больше не подходит
        else:
            target = hit
        if (op.category_id, op.category_by, op.rule_id) != target:
            out.append((op, target))
    return out


def _detail(op: MoneyOperation, target: tuple, cats: dict) -> dict:
    before, after = cats.get(op.category_id), cats.get(target[0])
    return {"op_id": str(op.op_id), "day": op.day.isoformat(), "amount": str(Decimal(op.amount).quantize(Decimal("0.01"))),
            "counterparty": op.counterparty, "before": before.name if before else None,
            "after": after.name if after else None, "rule_id": str(target[2]) if target[2] else None,
            "by": target[1] or "none"}


async def apply_rules(db: AsyncSession, user_id: uuid.UUID, op_ids: list[uuid.UUID] | None = None,
                      trigger: str = "manual", rule_id: uuid.UUID | None = None) -> int:
    """Пересчитать статьи по правилам у операций без ручной разметки (все или op_ids). Изменилось — новая версия;
    пересчёт — запись в журнале применения. Коммит — у вызывающего. Возвращает число изменённых операций."""
    ops = await _eligible_ops(db, user_id, op_ids)
    changes = await _changes(db, user_id, ops)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    for op, target in changes:
        db.add(planning_svc._new_version(op, category_id=target[0], category_by=target[1], rule_id=target[2]))
    problems: list[dict] = []
    if op_ids is None:  # полный пересчёт — заодно проверяем сами правила
        rules = await _rules(db, user_id)
        accounts = {a.id: a for a in await planning_svc._accounts(db, user_id)}
        _, found = await _diagnose(db, user_id, rules, cats, accounts, ops)
        problems = [{"rule_id": str(k), "message": v} for k, v in found.items()]
    db.add(RuleRun(user_id=user_id, trigger=trigger, rule_id=rule_id, checked=len(ops), changed=len(changes),
                   details=[_detail(op, t, cats) for op, t in changes[:RUN_DETAILS]], problems=problems,
                   created_at=_now()))
    await db.flush()
    return len(changes)


async def dry_run(db: AsyncSession, user_id: uuid.UUID, limit: int = 10) -> dict:
    """Проверка перед массовым применением: что поменяют правила у всех операций без ручной разметки — без записи."""
    ops = await _eligible_ops(db, user_id)
    changes = await _changes(db, user_id, ops)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    rules = await _rules(db, user_id)
    accounts = {a.id: a for a in await planning_svc._accounts(db, user_id)}
    _, found = await _diagnose(db, user_id, rules, cats, accounts, ops)
    ref = await _referrer(db, user_id)
    return {"checked": len(ops), "changed": len(changes),
            "examples": _with_refs([_detail(op, t, cats) for op, t in changes[:limit]], ref),
            "problems": _with_refs([{"rule_id": str(k), "message": v} for k, v in found.items()], ref)}


async def log_import(db: AsyncSession, user_id: uuid.UUID, trigger: str, rows: list[tuple[MoneyOperation, bool]]) -> None:
    """Загрузка из банка: какие новые операции правила разнесли сразу — запись в журнале. rows — (операция,
    проверялась ли правилами); пишем, если хоть одна проверялась."""
    checked = [op for op, tried in rows if tried]
    if not checked:
        return
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    hits = [op for op in checked if op.category_id is not None]
    details = [{"op_id": str(op.op_id), "day": op.day.isoformat(), "amount": str(Decimal(op.amount).quantize(Decimal("0.01"))),
                "counterparty": op.counterparty, "before": None,
                "after": cats[op.category_id].name if op.category_id in cats else None,
                "rule_id": str(op.rule_id) if op.rule_id else None, "by": op.category_by or "none"}
               for op in hits[:RUN_DETAILS]]
    db.add(RuleRun(user_id=user_id, trigger=trigger, checked=len(checked), changed=len(hits), details=details,
                   problems=[], created_at=_now()))


async def _referrer(db: AsyncSession, user_id: uuid.UUID):
    """Ссылка на правило для журнала: номер и условия сейчас; удалённое — «удалено»."""
    rules = await _rules(db, user_id, deleted=True)
    accounts = {a.id: a.name for a in await planning_svc._accounts(db, user_id)}
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    live = [r for r in rules if r.deleted_at is None]
    number = {str(r.id): i + 1 for i, r in enumerate(live)}

    def ref(rule_id: str | None) -> dict | None:
        if not rule_id:
            return None
        r = next((x for x in rules if str(x.id) == rule_id), None)
        if r is None:
            return {"id": rule_id, "number": None, "text": "правило удалено"}
        cat = cats.get(r.category_id)
        text = f"{rule_text(r, accounts)} → {cat.name if cat else '—'}"
        return {"id": rule_id, "number": number.get(rule_id), "text": text + (" (удалено)" if r.deleted_at else "")}

    return ref


def _with_refs(items: list[dict], ref) -> list[dict]:
    return [{**x, "rule": ref(x.get("rule_id"))} for x in items]


async def runs(db: AsyncSession, user_id: uuid.UUID, limit: int = 50) -> list[dict]:
    """Журнал применения правил, новые сверху."""
    rows = list((await db.execute(select(RuleRun).where(RuleRun.user_id == user_id)
                                  .order_by(RuleRun.created_at.desc()).limit(limit))).scalars())
    ref = await _referrer(db, user_id)
    return [
        {"id": run.id, "created_at": run.created_at, "trigger": run.trigger,
         "title": TRIGGER_TEXT.get(run.trigger, run.trigger), "rule": ref(str(run.rule_id) if run.rule_id else None),
         "checked": run.checked, "changed": run.changed,
         "details": _with_refs(run.details, ref), "problems": _with_refs(run.problems, ref)}
        for run in rows
    ]


async def preview(db: AsyncSession, user_id: uuid.UUID, data: RuleIn, limit: int = 5) -> dict:
    """Сколько операций без ручной разметки подходит под условия — до сохранения правила."""
    data = await _check(db, user_id, data)
    rule = _probe(data, "preview")
    ops = await _eligible_ops(db, user_id)
    facts = await _facts(db, user_id, ops)
    hits = [op for op in ops if eng.fits(rule, facts[op.op_id])]
    return {"matched": len(hits), "examples": [
        {"op_id": op.op_id, "day": op.day, "amount": op.amount, "counterparty": op.counterparty, "comment": op.comment}
        for op in hits[:limit]
    ]}


# ---------- ручная разметка ----------


async def categorize(db: AsyncSession, user_id: uuid.UUID, op_ids: list[uuid.UUID], category_id: uuid.UUID | None) -> int:
    """Массовая разметка: статья ставится вручную (правила её больше не трогают); None — убрать статью (тогда
    операцию снова могут разнести правила). Выплаты Ozon и переводы — нельзя. Возвращает число изменённых."""
    if not op_ids:
        return 0
    cat = await planning_svc._category(db, user_id, category_id) if category_id else None
    if cat is not None and (cat.kind == "transfer" or cat.archived_at is not None):
        raise PlanningError("Выберите действующую статью поступлений или расходов")
    rows = list((await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id.in_(op_ids))))).scalars())
    if len(rows) != len(set(op_ids)):
        raise planning_svc.NotFound("Часть операций не найдена — обновите страницу")
    changed = 0
    for op in rows:
        if op.transfer_id is not None:
            raise PlanningError("Перевод между своими счетами не разносится по статьям — снимите его с выбора")
        if cat is not None and cat.kind != ("income" if op.amount > 0 else "expense"):
            word = "поступлений" if op.amount > 0 else "расходов"
            raise PlanningError(f"Среди выбранных есть {'поступления' if op.amount > 0 else 'платежи'} — для них нужна "
                                f"статья {word}. Выберите только поступления или только платежи")
        target = (cat.id, "user", None) if cat else (None, None, None)
        if (op.category_id, op.category_by, op.rule_id) == target:
            continue
        db.add(planning_svc._new_version(op, category_id=target[0], category_by=target[1], rule_id=target[2]))
        changed += 1
    await db.flush()
    return changed


async def suggestion(db: AsyncSession, user_id: uuid.UUID, op: MoneyOperation) -> dict | None:
    """После ручной разметки: «создать правило для всех операций этого контрагента?» — если у операции есть контрагент
    (ИНН из банка или название) и такого правила ещё нет. `others` — сколько операций без ручной разметки
    поменяет правило."""
    if op.category_id is None or op.category_by != "user" or op.transfer_id is not None or op.source == "ozon_payout":
        return None
    facts = (await _facts(db, user_id, [op]))[op.op_id]
    if not (facts.inn or eng.norm(facts.counterparty)):
        return None
    direction = "in" if op.amount > 0 else "out"
    proposal = eng.Rule("new", direction, str(op.category_id), counterparty_inn=facts.inn,
                        counterparty=None if facts.inn else facts.counterparty)
    for r in await _rules(db, user_id):
        same_party = (r.counterparty_inn and r.counterparty_inn == facts.inn) or (
            not facts.inn and r.counterparty and eng.norm(r.counterparty) == eng.norm(facts.counterparty))
        if same_party and r.direction == direction and not r.purpose and r.amount_min is None and r.amount_max is None:
            return None  # правило для этого контрагента уже есть
    ops = [o for o in (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.is_(None), MoneyOperation.source != "ozon_payout",
        MoneyOperation.op_id != op.op_id,
    )))).scalars() if _eligible(o)]
    all_facts = await _facts(db, user_id, ops)
    others = sum(1 for o in ops if eng.fits(proposal, all_facts[o.op_id]) and o.category_id != op.category_id)
    cat = await db.get(CashCategory, op.category_id)
    return {"direction": direction, "counterparty_inn": facts.inn,
            "counterparty": facts.counterparty if not facts.inn else None,
            "counterparty_name": facts.counterparty, "category": {"id": cat.id, "name": cat.name}, "others": others}
