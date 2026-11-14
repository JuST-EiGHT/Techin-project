"""Долг поставщику по поставкам (шаг 10.23a): чистый расчёт без БД и HTTP.

Поставщик получает деньги после продажи. Каждая проданная штука относится к поставке — FIFO внутри артикула: к самой
старой поставке, где этот артикул ещё остался (склад Ozon партии не различает — это правило учёта, как ФИФО на складе).
Продажа раньше даты поставки в неё не идёт; закрытая вручную поставка не берёт продажи после дня закрытия.

Продажа — начисление Ozon «Выручка»: оно появляется, когда покупатель получил товар, — невыкуп и возврат до получения
продажей не становятся. Возврат после получения («Возврат выручки») снимает продажу с той поставки, откуда ушло это
отправление (по номеру отправления); номера нет или отправление не из поставок — снимается последняя проданная штука
артикула. Возвращённая штука снова доступна в своей поставке.

Долг поставки = (продано + «уже продано ранее») × цена; цена — из справочника себестоимости на дату поставки с НДС
поставщика, нет цены — долг строки неизвестен (не 0). Оплаты — общим потоком по FIFO: сначала закрываются самые старые
поставки; оплачено больше, чем должны, — аванс.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
SELLING, AWAITING, DONE, CLOSED = "selling", "awaiting", "done", "closed"


def q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class LineIn:
    key: str  # ключ сопоставления артикула (offer_match.normalize или артикул товара)
    article: str  # как в документе
    qty: int
    sold_before: int = 0
    written_off: int = 0
    price: Decimal | None = None  # с НДС за штуку; None — нет себестоимости


@dataclass(frozen=True)
class LotIn:
    lot_id: str
    day: date
    number: str
    lines: tuple[LineIn, ...]
    closed_on: date | None = None  # закрыта вручную: продажи после этого дня не берёт, до него — остаются


@dataclass(frozen=True)
class Event:
    day: date
    key: str
    qty: int  # > 0
    kind: str  # sale | return
    posting: str | None = None
    seq: int = 0  # порядок внутри дня — как в исходных данных


@dataclass(frozen=True)
class Payment:
    day: date
    amount: Decimal  # меньше нуля — возврат денег от поставщика


@dataclass
class Move:
    day: date
    key: str
    qty: int  # + продажа в поставку, − возврат из неё
    posting: str | None


@dataclass
class LineOut:
    line: LineIn
    sold: int = 0  # продано (за вычетом возвратов) по данным сервиса
    returned: int = 0  # из них вернулось после получения

    @property
    def sold_total(self) -> int:
        return self.line.sold_before + self.sold

    @property
    def remaining(self) -> int:
        return self.line.qty - self.line.written_off - self.sold_total

    @property
    def amount(self) -> Decimal | None:
        return None if self.line.price is None else q(self.line.price * self.sold_total)

    @property
    def stock_value(self) -> Decimal | None:
        return None if self.line.price is None else q(self.line.price * max(self.remaining, 0))


@dataclass
class LotOut:
    lot: LotIn
    lines: list[LineOut]
    moves: list[Move] = field(default_factory=list)
    paid: Decimal = ZERO

    @property
    def qty(self) -> int:
        return sum(ln.line.qty for ln in self.lines)

    @property
    def sold(self) -> int:
        return sum(ln.sold_total for ln in self.lines)

    @property
    def remaining(self) -> int:
        return sum(max(ln.remaining, 0) for ln in self.lines)

    @property
    def debt(self) -> Decimal:
        """Начислено поставщику по известным ценам."""
        return q(sum((ln.amount for ln in self.lines if ln.amount is not None), ZERO))

    @property
    def unknown_price(self) -> int:
        """Проданных штук без цены — долг по ним неизвестен."""
        return sum(ln.sold_total for ln in self.lines if ln.line.price is None)

    @property
    def unpaid(self) -> Decimal:
        return q(self.debt - self.paid)

    @property
    def status(self) -> str:
        if self.lot.closed_on is not None:
            return CLOSED
        if self.remaining > 0:
            return SELLING
        if self.unpaid > 0 or self.unknown_price > 0:
            return AWAITING
        return DONE


@dataclass
class Result:
    lots: list[LotOut]
    paid_total: Decimal = ZERO
    prepaid: Decimal = ZERO  # оплачено больше, чем начислено
    unallocated: dict[str, int] = field(default_factory=dict)  # продано без остатка в поставках: ключ → штук
    orphan_returns: dict[str, int] = field(default_factory=dict)  # возвраты, которые не к чему отнести

    @property
    def to_pay(self) -> Decimal:
        return q(sum((lot.unpaid for lot in self.lots), ZERO))

    @property
    def accrued(self) -> Decimal:
        return q(sum((lot.debt for lot in self.lots), ZERO))


def _order(lot: LotIn) -> tuple:
    return (lot.day, lot.number, lot.lot_id)


def allocate(lots: list[LotIn], events: list[Event], payments: list[Payment]) -> Result:
    ordered = sorted(lots, key=_order)
    outs = [LotOut(lot, [LineOut(ln) for ln in lot.lines]) for lot in ordered]
    # артикул → строки поставок по порядку FIFO (у одной поставки артикул может встретиться дважды — обе строки)
    by_key: dict[str, list[tuple[LotOut, LineOut]]] = defaultdict(list)
    for out in outs:
        for line in out.lines:
            by_key[line.line.key].append((out, line))

    # куда ушли штуки: по отправлению и по артикулу (для возвратов), [поставка, строка, штук]
    by_posting: dict[tuple[str, str], list[list]] = defaultdict(list)
    by_article: dict[str, list[list]] = defaultdict(list)
    unalloc_posting: dict[tuple[str, str], int] = defaultdict(int)
    unallocated: dict[str, int] = defaultdict(int)
    orphan: dict[str, int] = defaultdict(int)

    for ev in sorted(events, key=lambda e: (e.day, 0 if e.kind == "sale" else 1, e.seq)):
        if ev.qty <= 0:
            continue
        if ev.kind == "sale":
            left = ev.qty
            for out, line in by_key.get(ev.key, []):
                if left == 0:
                    break
                closed = out.lot.closed_on is not None and ev.day > out.lot.closed_on
                if closed or out.lot.day > ev.day or line.remaining <= 0:
                    continue
                take = min(line.remaining, left)
                line.sold += take
                left -= take
                out.moves.append(Move(ev.day, ev.key, take, ev.posting))
                record = [out, line, take]
                by_article[ev.key].append(record)
                if ev.posting:
                    by_posting[(ev.posting, ev.key)].append(record)
            if left:
                unallocated[ev.key] += left
                if ev.posting:
                    unalloc_posting[(ev.posting, ev.key)] += left
            continue

        # возврат: сначала то, что ушло этим отправлением, потом — последняя проданная штука артикула
        left = ev.qty
        if ev.posting:
            pk = (ev.posting, ev.key)
            back = min(unalloc_posting.get(pk, 0), left)  # штуки отправления не из поставок — в долг и не входили
            if back:
                unalloc_posting[pk] -= back
                unallocated[ev.key] -= back
                left -= back
            left = _reverse(by_posting.get(pk, []), left, ev)
        if left:
            left = _reverse(by_article.get(ev.key, []), left, ev)
        if left:
            back = min(unallocated.get(ev.key, 0), left)
            unallocated[ev.key] -= back
            left -= back
        if left:
            orphan[ev.key] += left

    paid_total = q(sum((p.amount for p in payments), ZERO))
    pool = max(paid_total, ZERO)
    for out in outs:
        take = min(pool, out.debt)
        out.paid = q(take)
        pool -= take
    return Result(
        outs, paid_total=paid_total, prepaid=q(pool),
        unallocated={k: v for k, v in unallocated.items() if v > 0},
        orphan_returns={k: v for k, v in orphan.items() if v > 0},
    )


def _reverse(records: list[list], left: int, ev: Event) -> int:
    """Снять `left` штук с последних продаж из списка; вернуть, сколько не нашлось."""
    for record in reversed(records):
        if left == 0:
            break
        out, line, qty = record
        if qty <= 0:
            continue
        take = min(qty, left)
        record[2] -= take
        line.sold -= take
        line.returned += take
        left -= take
        out.moves.append(Move(ev.day, ev.key, -take, ev.posting))
    return left
