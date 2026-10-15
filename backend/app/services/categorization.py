"""Правила разнесения операций по статьям (шаг 3.12).

- Ручная разметка важнее правил: статью, поставленную пользователем (`category_by = user`), правила не трогают.
- Встроенные правила раньше пользовательских: маркетплейс по ИНН и перевод между своими счетами ставятся при загрузке
  выписки (`category_by = auto`); налоги по реквизитам казначейства — здесь же, при разнесении.
- Новое или изменённое правило пересчитывает только операции без ручной разметки; операция, которой статью поставило
  удалённое правило, остаётся без статьи (или получает статью следующего подошедшего правила). Изменение статьи —
  новая версия операции (правило 12).
- Правила работают для операций из банка (файл — шаг 3.11, API — 3.12), из загрузки файлом (3.8) и внесённых вручную
  без статьи. Выплаты Ozon и переводы между своими счетами правила не трогают.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import categorize as eng
from app.models.bank import BankTransaction
from app.models.planning import CashCategory, CategorizationRule, MoneyOperation
from app.services import planning as planning_svc
from app.services.planning import PlanningError, _current

MAX_RULES = 300
DIRECTION_KIND = {"in": "income", "out": "expense"}
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


async def _rules(db: AsyncSession, user_id: uuid.UUID) -> list[CategorizationRule]:
    res = await db.execute(select(CategorizationRule).where(
        CategorizationRule.user_id == user_id, CategorizationRule.deleted_at.is_(None),
    ).order_by(CategorizationRule.priority, CategorizationRule.created_at))
    return list(res.scalars())


def _engine_rule(r: CategorizationRule) -> eng.Rule:
    return eng.Rule(
        id=str(r.id), direction=r.direction, category_id=str(r.category_id), priority=r.priority,
        counterparty_inn=r.counterparty_inn, counterparty=r.counterparty, purpose=r.purpose,
        amount_min=None if r.amount_min is None else Decimal(r.amount_min),
        amount_max=None if r.amount_max is None else Decimal(r.amount_max), enabled=r.enabled,
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
    probe = eng.Rule("", data.direction, "", counterparty_inn=data.counterparty_inn, counterparty=data.counterparty,
                     purpose=data.purpose, amount_min=data.amount_min, amount_max=data.amount_max)
    if not eng.has_conditions(probe):
        raise PlanningError("Укажите хотя бы одно условие: ИНН, контрагента, слова в назначении или сумму")
    return data


async def create_rule(db: AsyncSession, user_id: uuid.UUID, data: RuleIn) -> CategorizationRule:
    data = await _check(db, user_id, data)
    rules = await _rules(db, user_id)
    if len(rules) >= MAX_RULES:
        raise PlanningError(f"Правил уже {MAX_RULES} — удалите ненужные")
    rule = CategorizationRule(
        id=uuid.uuid4(), user_id=user_id, priority=(max((r.priority for r in rules), default=0) + 10),
        direction=data.direction, counterparty_inn=data.counterparty_inn, counterparty=data.counterparty,
        purpose=data.purpose, amount_min=data.amount_min, amount_max=data.amount_max, category_id=data.category_id,
        enabled=data.enabled,
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
              "enabled")
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


async def rules_out(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    rules = await _rules(db, user_id)
    cats = {c.id: c for c in await planning_svc._categories(db, user_id)}
    counts = dict((await db.execute(_current(select(MoneyOperation.rule_id, func.count()).where(
        MoneyOperation.user_id == user_id, MoneyOperation.category_by == "rule",
        MoneyOperation.rule_id.in_([r.id for r in rules]),
    )).group_by(MoneyOperation.rule_id))).all()) if rules else {}
    return [
        {"id": r.id, "priority": r.priority, "direction": r.direction, "counterparty_inn": r.counterparty_inn,
         "counterparty": r.counterparty, "purpose": r.purpose, "amount_min": r.amount_min, "amount_max": r.amount_max,
         "category": {"id": r.category_id, "name": cats[r.category_id].name, "kind": cats[r.category_id].kind}
         if r.category_id in cats else None,
         "enabled": r.enabled, "operations": counts.get(r.id, 0)}
        for r in rules
    ]


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


async def apply_rules(db: AsyncSession, user_id: uuid.UUID, op_ids: list[uuid.UUID] | None = None) -> int:
    """Пересчитать статьи по правилам у операций без ручной разметки (все или op_ids). Изменилось — новая версия.
    Коммит — у вызывающего. Возвращает число изменённых операций."""
    q = _current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.is_(None),
        MoneyOperation.source != "ozon_payout",
    ))
    if op_ids is not None:
        q = q.where(MoneyOperation.op_id.in_(op_ids))
    ops = [op for op in (await db.execute(q)).scalars() if _eligible(op)]
    if not ops:
        return 0
    facts = await _facts(db, user_id, ops)
    pk = await picker(db, user_id, list(facts.values()))
    changed = 0
    for op in ops:
        hit = pk.pick(facts[op.op_id])
        if hit is None:
            if op.category_by != "rule":
                continue  # статьи не было — и не появилось
            target = (None, None, None)  # правило удалено или больше не подходит
        else:
            target = hit
        if (op.category_id, op.category_by, op.rule_id) == target:
            continue
        db.add(planning_svc._new_version(op, category_id=target[0], category_by=target[1], rule_id=target[2]))
        changed += 1
    if changed:
        await db.flush()
    return changed


async def preview(db: AsyncSession, user_id: uuid.UUID, data: RuleIn, limit: int = 5) -> dict:
    """Сколько операций без ручной разметки подходит под условия — до сохранения правила."""
    data = await _check(db, user_id, data)
    rule = eng.Rule("preview", data.direction, str(data.category_id), counterparty_inn=data.counterparty_inn,
                    counterparty=data.counterparty, purpose=data.purpose, amount_min=data.amount_min,
                    amount_max=data.amount_max)
    ops = [op for op in (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id.is_(None), MoneyOperation.source != "ozon_payout",
    )).order_by(MoneyOperation.day.desc()))).scalars() if _eligible(op)]
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
