"""ABC-анализ товаров (шаг 10.10, BUSINESS_LOGIC §10). Чистый модуль — без БД и HTTP.

Товары с показателем больше нуля сортируются по убыванию; накопленная доля — от суммы положительных значений.
Группа товара — по накопленной доле **до** него: меньше границы A (80 %) — A, меньше границы B (95 %) — B, иначе C.
Товар, на котором накопленная доля переходит границу, остаётся в старшей группе: A — товары, без которых первые 80 % не
набрать; ровно на границе (до товара уже 80 %) — уже следующая группа. Одинаковые значения — в одной группе (по первому
из них), чтобы порядок равных не решал группу. Первый товар — всегда A, поэтому единственный товар — A.
Ноль и минус (убыток по прибыли, возвраты больше продаж по выручке) — C; минус — с пометкой «убыточный», его доля не
считается. Значение неизвестно (прибыль без себестоимости, правило 5) — группы нет.
"""
from dataclasses import dataclass, field
from decimal import Decimal

A, B, C = "A", "B", "C"
GROUPS = (A, B, C)
DEFAULT_A = Decimal(80)
DEFAULT_B = Decimal(95)
ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Bounds:
    a: Decimal = DEFAULT_A  # накопленная доля, %, до которой товар — A
    b: Decimal = DEFAULT_B  # до которой — B

    def group(self, before: Decimal) -> str:
        return A if before < self.a else B if before < self.b else C


@dataclass(frozen=True)
class Row:
    key: str
    value: Decimal | None
    group: str | None  # None — значение неизвестно
    rank: int | None = None  # место среди положительных (1 — самый большой)
    share: Decimal | None = None  # доля в сумме положительных, %
    cum_share: Decimal | None = None  # накопленная доля с этим товаром, %
    loss: bool = False  # значение меньше нуля


@dataclass
class GroupSummary:
    group: str
    count: int = 0
    value: Decimal = ZERO  # сумма показателя (у C — с убыточными)
    share: Decimal = ZERO  # доля в сумме положительных, %
    product_share: Decimal = ZERO  # доля товаров группы среди товаров с группой, %
    keys: list[str] = field(default_factory=list)


def classify(values: dict[str, Decimal | None], bounds: Bounds = Bounds()) -> list[Row]:
    """Группы товаров по показателю: сначала положительные по убыванию (равные — по ключу), затем ноль и минус, затем
    неизвестные."""
    positive = sorted(((k, v) for k, v in values.items() if v is not None and v > 0), key=lambda kv: (-kv[1], kv[0]))
    total = sum((v for _, v in positive), ZERO)
    rows: list[Row] = []
    cum = ZERO
    block_value: Decimal | None = None
    block_group = A
    for rank, (key, v) in enumerate(positive, start=1):
        if v != block_value:  # новый блок равных значений — группа по накопленной доле до него
            block_value, block_group = v, bounds.group(cum / total * HUNDRED)
        cum += v
        rows.append(Row(key, v, block_group, rank, v / total * HUNDRED, cum / total * HUNDRED))
    rest = sorted(((k, v) for k, v in values.items() if v is not None and v <= 0), key=lambda kv: (-kv[1], kv[0]))
    rows += [Row(k, v, C, share=ZERO if v == 0 else None, loss=v < 0) for k, v in rest]
    rows += [Row(k, None, None) for k in sorted(k for k, v in values.items() if v is None)]
    return rows


def summary(rows: list[Row]) -> list[GroupSummary]:
    """Сколько товаров и какой вклад у каждой группы (A, B, C — всегда все три)."""
    out = {g: GroupSummary(g) for g in GROUPS}
    known = [r for r in rows if r.group is not None]
    for r in known:
        s = out[r.group]
        s.count += 1
        s.value += r.value
        s.share += r.share or ZERO
        s.keys.append(r.key)
    for s in out.values():
        s.product_share = ZERO if not known else Decimal(s.count) / len(known) * HUNDRED
    return [out[g] for g in GROUPS]


def matrix(first: dict[str, str | None], second: dict[str, str | None]) -> dict[tuple[str, str | None], list[str]]:
    """Сравнение двух ABC (например, по выручке и по прибыли): (группа по первому, группа по второму или None —
    неизвестна) → товары. Товары без группы по первому не входят."""
    out: dict[tuple[str, str | None], list[str]] = {}
    for key in sorted(first):
        g1 = first[key]
        if g1 is None:
            continue
        out.setdefault((g1, second.get(key)), []).append(key)
    return out


def change_pct(value: Decimal | None, prev: Decimal | None) -> Decimal | None:
    """Изменение к прошлому периоду, %; прошлого нет или он ноль — None."""
    if value is None or prev is None or prev == 0:
        return None
    return (value - prev) / abs(prev) * HUNDRED
