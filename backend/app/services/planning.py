"""Планирование (шаг 3.8): включение раздела и мастер, счета и остатки, статьи, операции, выплаты Ozon как операции.

Остаток счёта на сегодня = начальный остаток (на начало дня `as_of`, последняя запись) + действующие операции счёта
с `as_of` по сегодня. Начального остатка нет — остаток неизвестен (None, а не 0). Операции до `as_of` видны в списке,
но в остаток не входят: они уже учтены в начальном остатке.
Выплаты Ozon (шаг 3.6) сами становятся поступлениями на счёт «куда приходят выплаты» (`sync_ozon_operations`): одна
операция на день выплаты (`external_key`), изменилась сумма или счёт — новая версия; вручную не вводятся.
Коммит — на вызывающем, кроме функций, которые сами пишут несколько шагов (помечено).
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ozon_balance import OzonBalanceDay
from app.models.planning import (
    ACCOUNT_KINDS,
    CATEGORY_KINDS,
    COMMENT_MAX,
    DEFAULT_HORIZON_DAYS,
    CashCategory,
    MoneyAccount,
    MoneyAccountBalance,
    MoneyOperation,
    PlanningSettings,
)
from app.models.user_settings import COST_LABELS
from app.services import user_settings as settings_svc
from app.services.sync.finance import today_msk

TEMPLATE_FILE = Path(__file__).resolve().parents[1] / "data" / "cash_categories.json"
MAX_AMOUNT = Decimal("10000000000")
MIN_DAY = date(2000, 1, 1)
OZON_KEY = "ozon_payout:"
OZON_BANK_DAYS = 5  # выплата Ozon доходит до счёта за 0–5 дней (шаг 3.11)
KIND_TITLES = {"checking": "Расчётный счёт", "card": "Карта", "cash": "Наличные", "wallet": "Кошелёк", "other": "Счёт"}


class PlanningError(Exception):
    """Запрос не подходит. Текст — для пользователя."""


class NotFound(PlanningError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- шаблон статей ----------


@lru_cache(maxsize=1)
def _template() -> dict:
    return json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def template_categories() -> list[dict]:
    return _template()["categories"]


def suggested_groups(supplier_payment: str | None) -> list[str]:
    """Какие группы статей предложить по ответу «Откуда товар»; вопрос пропущен — только общие."""
    return _template()["groups_by_supplier"][supplier_payment or ""]


REQUIRED_CODES = tuple(c["code"] for c in template_categories() if c.get("required"))


# ---------- настройки раздела ----------


async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> PlanningSettings | None:
    return await db.get(PlanningSettings, user_id)


async def state(db: AsyncSession, user_id: uuid.UUID) -> dict:
    s = await get_settings(db, user_id)
    us = await settings_svc.get_settings(db, user_id)
    return {
        "enabled": bool(s and s.enabled),
        "setup_done": bool(s and s.setup_at),
        "ozon_account_id": s.ozon_account_id if s else None,
        "threshold": s.threshold if s else None,
        "horizon_days": s.horizon_days if s else DEFAULT_HORIZON_DAYS,
        "supplier_payment": us.supplier_payment,
        "supplier_deferral_days": us.supplier_deferral_days,
        "cost_label": COST_LABELS[us.supplier_payment],
        "gen_ozon": s.gen_ozon if s else True,
        "gen_supplier": s.gen_supplier if s else True,
        "gen_taxes": s.gen_taxes if s else True,
        "supplier_period": s.supplier_period if s else "week",
        "supplier_day": s.supplier_day if s else 1,
    }


async def _account(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID, active: bool = True) -> MoneyAccount:
    acc = await db.get(MoneyAccount, account_id)
    if acc is None or acc.user_id != user_id:
        raise NotFound("Счёт не найден")
    if active and acc.archived_at is not None:
        raise PlanningError(f"Счёт «{acc.name}» в архиве — выберите другой")
    return acc


async def update_settings(db: AsyncSession, user_id: uuid.UUID, changes: dict) -> None:
    """enabled, ozon_account_id (None — выплаты Ozon никуда не записываются), threshold, horizon_days, генераторы плана
    и день оплаты поставщику (шаг 3.9). Коммит — здесь."""
    s = await get_settings(db, user_id)
    if s is None or s.setup_at is None:
        if changes.get("enabled"):
            raise PlanningError("Раздел ещё не настроен — ответьте на несколько вопросов на странице «Планирование»")
        if s is None:
            s = PlanningSettings(user_id=user_id, enabled=False, horizon_days=DEFAULT_HORIZON_DAYS)
            db.add(s)
    if changes.get("ozon_account_id") is not None:
        await _account(db, user_id, changes["ozon_account_id"])
    period = changes.get("supplier_period") or s.supplier_period or "week"
    day = changes.get("supplier_day", s.supplier_day)
    if day is not None and period == "week" and not 1 <= day <= 7:
        if "supplier_day" in changes:
            raise PlanningError("День оплаты поставщику — день недели от 1 (понедельник) до 7 (воскресенье)")
        changes["supplier_day"] = 1
    for key in ("gen_ozon", "gen_supplier", "gen_taxes", "supplier_period", "supplier_day"):
        if key in changes and changes[key] is None:
            del changes[key]  # null — не менять: у этих полей нет «пусто»
    for key, value in changes.items():
        setattr(s, key, value)
    await db.commit()
    await sync_ozon_operations(db, user_id)


# ---------- мастер ----------


@dataclass
class SetupAccount:
    name: str
    kind: str
    balance: Decimal | None = None  # пусто — остаток не указан (вопрос пропущен)


@dataclass
class SetupIn:
    supplier_payment: str | None = None  # None — вопрос пропущен: настройку не меняем
    supplier_deferral_days: int | None = None
    accounts: list[SetupAccount] = field(default_factory=list)
    ozon_account: int | None = None  # номер счёта в accounts; None — вопрос пропущен
    categories: list[str] = field(default_factory=list)  # коды статей из шаблона (обязательные добавятся сами)


def _check_account(name: str, kind: str) -> str:
    name = (name or "").strip()
    if not name:
        raise PlanningError("Укажите название счёта")
    if len(name) > 100:
        raise PlanningError("Название счёта — не длиннее 100 символов")
    if kind not in ACCOUNT_KINDS:
        raise PlanningError("Тип счёта: недопустимое значение")
    return name


def _check_amount(amount: Decimal, what: str = "Сумма") -> Decimal:
    if abs(amount) >= MAX_AMOUNT:
        raise PlanningError(f"{what}: слишком большое число — проверьте")
    return amount.quantize(Decimal("0.01"))


async def setup(db: AsyncSession, user_id: uuid.UUID, data: SetupIn) -> None:
    """Первый вход: профиль, счета с остатками, куда приходят выплаты Ozon, статьи. Коммит — здесь."""
    if data.supplier_payment is not None:
        try:
            await settings_svc.update_settings(db, user_id, {
                "supplier_payment": data.supplier_payment, "supplier_deferral_days": data.supplier_deferral_days,
            })
        except settings_svc.SettingsError as exc:
            raise PlanningError(str(exc)) from exc
    accounts = data.accounts or [SetupAccount(KIND_TITLES["checking"], "checking")]  # без счёта операции некуда записать
    if data.ozon_account is not None and not 0 <= data.ozon_account < len(accounts):
        raise PlanningError("Куда приходят выплаты Ozon: выберите один из ваших счетов")
    known = {c["code"] for c in template_categories()}
    if unknown := [c for c in data.categories if c not in known]:
        raise PlanningError(f"Статья не из шаблона: {', '.join(unknown)}")

    today = today_msk()
    created: list[MoneyAccount] = []
    for a in accounts:
        acc = MoneyAccount(id=uuid.uuid4(), user_id=user_id, name=_check_account(a.name, a.kind), kind=a.kind,
                           source="manual")
        db.add(acc)
        created.append(acc)
        if a.balance is not None:
            db.add(MoneyAccountBalance(user_id=user_id, account_id=acc.id, amount=_check_amount(a.balance, "Остаток"),
                                       as_of=today))
    await add_template_categories(db, user_id, [*REQUIRED_CODES, *data.categories])

    s = await get_settings(db, user_id)
    if s is None:
        s = PlanningSettings(user_id=user_id, horizon_days=DEFAULT_HORIZON_DAYS)
        db.add(s)
    s.enabled, s.setup_at = True, _now()
    s.ozon_account_id = created[data.ozon_account].id if data.ozon_account is not None else None
    await db.commit()
    await sync_ozon_operations(db, user_id)


# ---------- статьи ----------


async def _categories(db: AsyncSession, user_id: uuid.UUID) -> list[CashCategory]:
    res = await db.execute(
        select(CashCategory).where(CashCategory.user_id == user_id).order_by(CashCategory.created_at, CashCategory.name)
    )
    return list(res.scalars())


async def add_template_categories(db: AsyncSession, user_id: uuid.UUID, codes: list[str]) -> int:
    """Статьи из шаблона, которых у пользователя ещё нет (по коду); статья из архива возвращается."""
    have = {c.code: c for c in await _categories(db, user_id) if c.code}
    added = 0
    for t in template_categories():
        if t["code"] not in codes:
            continue
        if (existing := have.get(t["code"])) is not None:
            if existing.archived_at is not None:
                existing.archived_at = None
                added += 1
            continue
        db.add(CashCategory(user_id=user_id, name=t["name"], kind=t["kind"], code=t["code"]))
        added += 1
    return added


async def categories(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    why = {t["code"]: t["why"] for t in template_categories()}
    return [
        {"id": c.id, "name": c.name, "kind": c.kind, "code": c.code, "archived": c.archived_at is not None,
         "required": c.code in REQUIRED_CODES, "why": why.get(c.code)}
        for c in await _categories(db, user_id)
    ]


async def _category(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID) -> CashCategory:
    cat = await db.get(CashCategory, category_id)
    if cat is None or cat.user_id != user_id:
        raise NotFound("Статья не найдена")
    return cat


def _check_category_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise PlanningError("Укажите название статьи")
    if len(name) > 100:
        raise PlanningError("Название статьи — не длиннее 100 символов")
    return name


def name_key(name: str) -> str:
    """Название статьи для сравнения: без регистра, «ё» = «е», лишние пробелы не важны."""
    return " ".join(name.lower().replace("ё", "е").split())


async def _same_name(
    db: AsyncSession, user_id: uuid.UUID, name: str, kind: str, skip: uuid.UUID | None = None
) -> CashCategory | None:
    return next((
        c for c in await _categories(db, user_id)
        if c.archived_at is None and c.kind == kind and name_key(c.name) == name_key(name) and c.id != skip
    ), None)


async def create_category(db: AsyncSession, user_id: uuid.UUID, name: str, kind: str) -> CashCategory:
    name = _check_category_name(name)
    if kind not in ("income", "expense"):
        raise PlanningError("Статья — поступление или расход")
    if (same := await _same_name(db, user_id, name, kind)) is not None:
        raise PlanningError(f"Статья «{same.name}» уже есть — выберите её в списке")
    cat = CashCategory(user_id=user_id, name=name, kind=kind)
    db.add(cat)
    return cat


async def resolve_category(db: AsyncSession, user_id: uuid.UUID, name: str, kind: str) -> tuple[CashCategory, str]:
    """Статья, вписанная в форму операции: такая уже есть — она (existing); есть в архиве — возвращается (restored);
    нет — новая статья, дальше её можно выбрать из списка (created)."""
    name = _check_category_name(name)
    if kind not in ("income", "expense"):
        raise PlanningError("Статья — поступление или расход")
    same = [c for c in await _categories(db, user_id) if c.kind == kind and name_key(c.name) == name_key(name)]
    if active := next((c for c in same if c.archived_at is None), None):
        return active, "existing"
    if same:
        same[0].archived_at = None
        return same[0], "restored"
    cat = CashCategory(id=uuid.uuid4(), user_id=user_id, name=name, kind=kind)
    db.add(cat)
    await db.flush()
    return cat, "created"


async def update_category(
    db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID, name: str | None, archived: bool | None
) -> CashCategory:
    cat = await _category(db, user_id, category_id)
    if name is not None:
        name = _check_category_name(name)
        if (same := await _same_name(db, user_id, name, cat.kind, skip=cat.id)) is not None:
            raise PlanningError(f"Статья «{same.name}» уже есть")
        cat.name = name
    if archived is not None:
        if archived and cat.code in REQUIRED_CODES:
            raise PlanningError(
                "Эта статья нужна сервису: в неё попадают выплаты Ozon" if cat.code == "marketplace"
                else "Эта статья нужна сервису: так отмечаются переводы между своими счетами"
            )
        cat.archived_at = _now() if archived else None
    return cat


# ---------- счета ----------


async def _accounts(db: AsyncSession, user_id: uuid.UUID) -> list[MoneyAccount]:
    res = await db.execute(select(MoneyAccount).where(MoneyAccount.user_id == user_id).order_by(MoneyAccount.created_at))
    return list(res.scalars())


async def _openings(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, MoneyAccountBalance]:
    """Действующий начальный остаток каждого счёта — последняя запись."""
    res = await db.execute(
        select(MoneyAccountBalance).where(MoneyAccountBalance.user_id == user_id)
        .order_by(MoneyAccountBalance.created_at, MoneyAccountBalance.id)
    )
    return {b.account_id: b for b in res.scalars()}


def _current(q):
    return q.where(MoneyOperation.superseded_at.is_(None), MoneyOperation.deleted_at.is_(None))


async def balances(db: AsyncSession, user_id: uuid.UUID, on: date | None = None) -> dict[uuid.UUID, Decimal | None]:
    """Остаток каждого счёта на конец дня `on` (по умолчанию сегодня). Нет начального остатка — None."""
    on = on or today_msk()
    openings = await _openings(db, user_id)
    out: dict[uuid.UUID, Decimal | None] = {}
    for acc in await _accounts(db, user_id):
        opening = openings.get(acc.id)
        if opening is None:
            out[acc.id] = None
            continue
        total = await db.scalar(_current(
            select(func.coalesce(func.sum(MoneyOperation.amount), 0)).where(
                MoneyOperation.user_id == user_id, MoneyOperation.account_id == acc.id,
                MoneyOperation.day >= opening.as_of, MoneyOperation.day <= on,
            )
        ))
        out[acc.id] = (Decimal(opening.amount) + Decimal(total)).quantize(Decimal("0.01"))
    return out


async def accounts(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    s = await get_settings(db, user_id)
    openings = await _openings(db, user_id)
    bal = await balances(db, user_id)
    return [
        {
            "id": a.id, "name": a.name, "kind": a.kind, "source": a.source, "archived": a.archived_at is not None,
            "opening": {"amount": openings[a.id].amount, "as_of": openings[a.id].as_of} if a.id in openings else None,
            "balance": bal[a.id], "receives_ozon": bool(s and s.ozon_account_id == a.id),
            "number": a.number, "bank_name": a.bank_name,
        }
        for a in await _accounts(db, user_id)
    ]


async def create_account(
    db: AsyncSession, user_id: uuid.UUID, name: str, kind: str, balance: Decimal | None, as_of: date | None
) -> MoneyAccount:
    acc = MoneyAccount(id=uuid.uuid4(), user_id=user_id, name=_check_account(name, kind), kind=kind, source="manual")
    db.add(acc)
    if balance is not None:
        db.add(MoneyAccountBalance(user_id=user_id, account_id=acc.id, amount=_check_amount(balance, "Остаток"),
                                   as_of=_check_day(as_of or today_msk(), "Дата остатка")))
    return acc


async def update_account(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID, changes: dict) -> MoneyAccount:
    """name, kind — на месте; balance + as_of — новая запись начального остатка (правило 12); archived — архив."""
    acc = await _account(db, user_id, account_id, active=False)
    if "name" in changes or "kind" in changes:
        acc.name = _check_account(changes.get("name", acc.name), changes.get("kind", acc.kind))
        acc.kind = changes.get("kind", acc.kind)
    if changes.get("balance") is not None:
        current = (await _openings(db, user_id)).get(acc.id)
        amount = _check_amount(changes["balance"], "Остаток")
        as_of = _check_day(changes.get("as_of") or (current.as_of if current else today_msk()), "Дата остатка")
        if current is None or (Decimal(current.amount), current.as_of) != (amount, as_of):
            db.add(MoneyAccountBalance(user_id=user_id, account_id=acc.id, amount=amount, as_of=as_of))
    if changes.get("archived") is not None:
        if changes["archived"]:
            s = await get_settings(db, user_id)
            if s is not None and s.ozon_account_id == acc.id:
                raise PlanningError("Сюда приходят выплаты Ozon — сначала выберите для них другой счёт")
            acc.archived_at = _now()
        else:
            acc.archived_at = None
    return acc


# ---------- операции ----------


def _check_day(day: date, what: str = "Дата операции") -> date:
    if day < MIN_DAY:
        raise PlanningError(f"{what}: слишком ранняя дата — проверьте год")
    if day > today_msk():
        raise PlanningError(f"{what} в будущем — здесь только то, что уже было; плановые платежи — в «Календаре»")
    return day


def _clean(text: str | None, limit: int) -> str | None:
    text = (text or "").strip()
    return text[:limit] or None


@dataclass
class OperationIn:
    day: date
    amount: Decimal  # + приход, − расход
    account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    counterparty: str | None = None
    comment: str | None = None


async def _check_operation(db: AsyncSession, user_id: uuid.UUID, data: OperationIn, keep_category: uuid.UUID | None = None):
    _check_day(data.day)
    if data.amount == 0:
        raise PlanningError("Сумма не может быть нулевой")
    data.amount = _check_amount(data.amount)
    await _account(db, user_id, data.account_id)
    if data.category_id is not None:
        cat = await _category(db, user_id, data.category_id)
        if cat.kind == "transfer":
            raise PlanningError("Перевод между своими счетами добавляется отдельно — «Перевод»")
        if cat.archived_at is not None and cat.id != keep_category:
            raise PlanningError(f"Статья «{cat.name}» в архиве — выберите другую")
    data.counterparty = _clean(data.counterparty, 200)
    data.comment = _clean(data.comment, COMMENT_MAX)


async def create_operation(
    db: AsyncSession, user_id: uuid.UUID, data: OperationIn, source: str = "manual"
) -> MoneyOperation:
    await _check_operation(db, user_id, data)
    op = MoneyOperation(
        op_id=uuid.uuid4(), version=1, user_id=user_id, account_id=data.account_id, day=data.day, amount=data.amount,
        category_id=data.category_id, counterparty=data.counterparty, comment=data.comment, source=source,
    )
    db.add(op)
    return op


async def _transfer_category(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    cats = {c.code: c for c in await _categories(db, user_id) if c.code}
    if "transfer" not in cats:
        await add_template_categories(db, user_id, ["transfer"])
        await db.flush()
        cats = {c.code: c for c in await _categories(db, user_id) if c.code}
    return cats["transfer"].id


async def create_transfer(
    db: AsyncSession, user_id: uuid.UUID, from_id: uuid.UUID, to_id: uuid.UUID, amount: Decimal, day: date,
    comment: str | None = None,
) -> tuple[MoneyOperation, MoneyOperation]:
    """Перевод между своими счетами: − со счёта, + на счёт, общий transfer_id. Не доход и не расход."""
    if from_id == to_id:
        raise PlanningError("Перевод — между разными счетами")
    if amount <= 0:
        raise PlanningError("Сумма перевода — больше нуля")
    amount = _check_amount(amount)
    _check_day(day)
    await _account(db, user_id, from_id)
    await _account(db, user_id, to_id)
    category = await _transfer_category(db, user_id)
    transfer_id = uuid.uuid4()
    legs = []
    for account_id, value in ((from_id, -amount), (to_id, amount)):
        op = MoneyOperation(
            op_id=uuid.uuid4(), version=1, user_id=user_id, account_id=account_id, day=day, amount=value,
            category_id=category, comment=_clean(comment, COMMENT_MAX), source="manual", transfer_id=transfer_id,
        )
        db.add(op)
        legs.append(op)
    return legs[0], legs[1]


async def current_operation(db: AsyncSession, user_id: uuid.UUID, op_id: uuid.UUID) -> MoneyOperation:
    row = (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id == op_id
    )))).scalar_one_or_none()
    if row is None:
        raise NotFound("Операция не найдена")
    return row


def _new_version(row: MoneyOperation, **changes) -> MoneyOperation:
    """Исправление — новая строка той же операции; прежняя остаётся в истории (правило 12)."""
    row.superseded_at = _now()
    fields = {k: getattr(row, k) for k in (
        "op_id", "user_id", "account_id", "day", "amount", "category_id", "counterparty", "comment", "source",
        "transfer_id", "external_key",
    )}
    return MoneyOperation(**{**fields, **changes}, version=row.version + 1)


async def _transfer_legs(db: AsyncSession, user_id: uuid.UUID, transfer_id: uuid.UUID) -> list[MoneyOperation]:
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.transfer_id == transfer_id
    )).order_by(MoneyOperation.amount))
    return list(res.scalars())  # сначала «−» (со счёта), потом «+»


EDITABLE = ("day", "amount", "account_id", "category_id", "counterparty", "comment")
OZON_EDITABLE = ("category_id", "counterparty", "comment")


async def update_operation(
    db: AsyncSession, user_id: uuid.UUID, op_id: uuid.UUID, changes: dict
) -> tuple[MoneyOperation, bool]:
    """Изменилось хоть что-то — новая версия (у перевода — обеих частей). Ничего — (текущая, False)."""
    row = await current_operation(db, user_id, op_id)
    if row.source == "ozon_payout" and any(k not in OZON_EDITABLE for k in changes):
        raise PlanningError(
            "Выплата Ozon обновляется сама по данным Ozon — изменить можно статью, контрагента и комментарий"
        )
    if row.source == "bank" and any(k not in OZON_EDITABLE for k in changes):
        raise PlanningError("Операция из выписки банка: сумма, дата и счёт — как в банке. Изменить можно статью, "
                            "контрагента и описание")
    if row.transfer_id is not None and row.source != "bank":
        return await _update_transfer(db, user_id, row, changes)
    data = OperationIn(**{k: changes.get(k, getattr(row, k)) for k in EDITABLE})
    if row.source in ("ozon_payout", "bank"):
        if data.category_id is not None:
            cat = await _category(db, user_id, data.category_id)
            if cat.kind == "transfer" and row.transfer_id is None:
                raise PlanningError("Перевод между своими счетами определяется по выписке — эта операция не перевод")
        data.counterparty, data.comment = _clean(data.counterparty, 200), _clean(data.comment, COMMENT_MAX)
    else:
        await _check_operation(db, user_id, data, keep_category=row.category_id)
    new = {k: getattr(data, k) for k in EDITABLE}
    if all(new[k] == getattr(row, k) for k in EDITABLE):
        return row, False
    version = _new_version(row, **new)
    db.add(version)
    return version, True


async def _update_transfer(db: AsyncSession, user_id: uuid.UUID, row: MoneyOperation, changes: dict):
    if any(k in changes for k in ("account_id", "category_id", "counterparty")):
        raise PlanningError("У перевода меняются сумма, дата и комментарий; другие счета — удалите перевод и добавьте заново")
    legs = await _transfer_legs(db, user_id, row.transfer_id)
    amount = abs(_check_amount(changes.get("amount", abs(legs[0].amount))))
    if amount == 0:
        raise PlanningError("Сумма перевода — больше нуля")
    day = _check_day(changes.get("day", row.day))
    comment = _clean(changes.get("comment", row.comment), COMMENT_MAX)
    if (amount, day, comment) == (abs(legs[0].amount), row.day, row.comment):
        return row, False
    result = row
    for leg in legs:
        version = _new_version(leg, amount=-amount if leg.amount < 0 else amount, day=day, comment=comment)
        db.add(version)
        if leg.op_id == row.op_id:
            result = version
    return result, True


async def delete_operation(db: AsyncSession, user_id: uuid.UUID, op_id: uuid.UUID) -> None:
    """Скрыть операцию (у перевода — обе части); строки остаются в истории."""
    row = await current_operation(db, user_id, op_id)
    if row.source == "ozon_payout":
        raise PlanningError("Выплату Ozon удалить нельзя — она приходит из данных Ozon. Если выплаты приходят на другой "
                            "счёт, выберите его в «Счетах»")
    if row.source == "bank":
        raise PlanningError("Операцию из выписки банка удалить нельзя. Если это дубль операции, внесённой вручную, — "
                            "объедините их в «Счетах» → «Возможные дубли»")
    legs = await _transfer_legs(db, user_id, row.transfer_id) if row.transfer_id else [row]
    for leg in legs:
        leg.deleted_at = _now()


async def history(db: AsyncSession, user_id: uuid.UUID, op_id: uuid.UUID) -> list[MoneyOperation]:
    res = await db.execute(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.op_id == op_id
    ).order_by(MoneyOperation.version.desc()))
    rows = list(res.scalars())
    if not rows:
        raise NotFound("Операция не найдена")
    return rows


async def list_operations(
    db: AsyncSession, user_id: uuid.UUID, date_from: date, date_to: date, account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None, search: str | None = None, limit: int = 100, offset: int = 0,
) -> dict:
    """Действующие операции периода, новые сверху. Перевод — одной строкой (частью «со счёта»), а при выборе счёта —
    частью этого счёта. Итоги — без переводов: они не доход и не расход."""
    q = _current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.day >= date_from, MoneyOperation.day <= date_to
    ))
    if account_id is not None:
        q = q.where(MoneyOperation.account_id == account_id)
    if category_id is not None:
        q = q.where(MoneyOperation.category_id == category_id)
    if search:
        like = f"%{search.strip().lower()}%"
        q = q.where(or_(func.lower(MoneyOperation.comment).like(like), func.lower(MoneyOperation.counterparty).like(like)))
    rows = list((await db.execute(q.order_by(MoneyOperation.day.desc(), MoneyOperation.created_at.desc()))).scalars())

    accounts_by_id = {a.id: a for a in await _accounts(db, user_id)}
    cats = {c.id: c for c in await _categories(db, user_id)}
    openings = await _openings(db, user_id)
    legs: dict[uuid.UUID, list[MoneyOperation]] = {}
    for leg in (await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id,
        MoneyOperation.transfer_id.in_([r.transfer_id for r in rows if r.transfer_id]),
    )))).scalars():
        legs.setdefault(leg.transfer_id, []).append(leg)

    # перевод — одной строкой (частью «со счёта»); вторая часть ещё не загружена из банка — видна сама
    visible = [r for r in rows if r.transfer_id is None or account_id is not None or r.amount < 0
               or not any(leg.op_id != r.op_id for leg in legs.get(r.transfer_id, []))]
    income = sum((r.amount for r in rows if r.transfer_id is None and r.amount > 0), Decimal(0))
    expense = sum((r.amount for r in rows if r.transfer_id is None and r.amount < 0), Decimal(0))

    def out(r: MoneyOperation) -> dict:
        acc = accounts_by_id[r.account_id]
        cat = cats.get(r.category_id)
        opening = openings.get(r.account_id)
        transfer = None
        if r.transfer_id:
            other = next((leg for leg in legs.get(r.transfer_id, []) if leg.op_id != r.op_id), None)
            other_acc = accounts_by_id.get(other.account_id) if other else None
            transfer = {"id": r.transfer_id, "direction": "out" if r.amount < 0 else "in",
                        "account": {"id": other_acc.id, "name": other_acc.name} if other_acc else None}
        return {
            "op_id": r.op_id, "version": r.version, "day": r.day, "amount": r.amount,
            "account": {"id": acc.id, "name": acc.name},
            "category": {"id": cat.id, "name": cat.name, "kind": cat.kind} if cat else None,
            "counterparty": r.counterparty, "comment": r.comment, "source": r.source, "transfer": transfer,
            "before_opening": opening is not None and r.day < opening.as_of,
        }

    return {
        "date_from": date_from, "date_to": date_to, "total": len(visible), "limit": limit, "offset": offset,
        "items": [out(r) for r in visible[offset:offset + limit]],
        "totals": {k: Decimal(v).quantize(Decimal("0.01")) for k, v in
                   (("income", income), ("expense", expense), ("net", income + expense))},
    }


# ---------- выплаты Ozon → операции ----------


async def sync_ozon_operations(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Выплаты Ozon по дням (шаг 3.6) — поступления на счёт «куда приходят выплаты». Без дублей: одна операция на
    день (external_key); сумма или счёт изменились — новая версия, статья и комментарий пользователя сохраняются;
    выплаты больше нет в данных Ozon — операция скрывается. Подтверждённая выпиской банка (шаг 3.11, источник bank) —
    факт банка: её не меняем и не скрываем; поступление Ozon из выписки без пары — становится этой выплатой, второй
    операции нет. Коммит — здесь. Возвращает число изменений."""
    s = await get_settings(db, user_id)
    if s is None or not s.enabled or s.ozon_account_id is None:
        return 0
    res = await db.execute(
        select(OzonBalanceDay).where(OzonBalanceDay.user_id == user_id).order_by(OzonBalanceDay.day, OzonBalanceDay.synced_at)
    )
    days = {r.day: r for r in res.scalars()}  # последний снимок дня перекрывает
    payouts = {d: -Decimal(r.payments) for d, r in days.items() if r.payments}
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.external_key.like(f"{OZON_KEY}%")
    )))
    existing = {r.external_key: r for r in res.scalars()}
    res = await db.execute(_current(select(MoneyOperation).where(
        MoneyOperation.user_id == user_id, MoneyOperation.source == "bank", MoneyOperation.external_key.like("ozon_bank:%")
    )).order_by(MoneyOperation.day))
    from_bank = list(res.scalars())  # поступления Ozon из выписки, ещё без выплаты по данным Ozon
    marketplace = next((c.id for c in await _categories(db, user_id) if c.code == "marketplace" and not c.archived_at), None)
    changes = 0
    for day, amount in sorted(payouts.items()):
        key = f"{OZON_KEY}{day.isoformat()}"
        row = existing.pop(key, None)
        amount = amount.quantize(Decimal("0.01"))
        if row is None:
            bank = next((
                op for op in from_bank
                if 0 <= (op.day - day).days <= OZON_BANK_DAYS
                and abs(Decimal(op.amount) - amount) <= max(Decimal(1), amount / 100)
            ), None)
            if bank is not None:
                from_bank.remove(bank)
                db.add(_new_version(bank, external_key=key))
            else:
                db.add(MoneyOperation(
                    op_id=uuid.uuid4(), version=1, user_id=user_id, account_id=s.ozon_account_id, day=day,
                    amount=amount, category_id=marketplace, counterparty="Ozon", source="ozon_payout", external_key=key,
                ))
        elif row.source != "ozon_payout":
            continue  # подтверждена выпиской банка — факт банка не меняем (расхождение — в сверке)
        elif Decimal(row.amount) != amount or row.account_id != s.ozon_account_id:
            db.add(_new_version(row, amount=amount, account_id=s.ozon_account_id))
        else:
            continue
        changes += 1
    for row in existing.values():
        if row.source == "ozon_payout":
            row.deleted_at = _now()
            changes += 1
    if changes:
        await db.commit()
    return changes
