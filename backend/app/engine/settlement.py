"""Расчёты с Ozon (шаг 10.23): сверка каждой выплаты Ozon с поступлением на счёт и расхождения с объяснением. Чистый
модуль — без БД и HTTP.

Сверка выплаты (баланс Ozon по дням, 3.6) с поступлением от Ozon в выписке (3.11):
1. пары, связанные при загрузке выписки — поступление стало операцией этой выплаты (сумма ± 1 % или 1 ₽, 0–5 дней);
2. вторым проходом — поступление от Ozon без пары в окне 0–5 дней после выплаты без пары: ближайшее по сумме, при равной
   разнице — раньше; так находится «пришло другой суммой», которое загрузка выписки не связала (разница больше 1 %);
3. пары нет: выписка за дни выплаты + 5 загружена — «не пришло», нет (ещё не загружена или выплата раньше первой
   выписки) — «нет выписки»; поступление без выплаты — «нет в данных Ozon»; выписок нет вовсе — сверки нет («без банка»).
Пришло ровно (до копейки) — «пришло», иначе — «другая сумма». Расхождения: не пришло, другая сумма (от 1 ₽ — копейки
округления не расхождение), поступление без выплаты, выплата разошлась с графиком (больше порога пользователя, 10 %),
просроченная по графику выплата, расхождение дней баланса с финансовым отчётом Ozon за полмесяца.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
WINDOW_DAYS = 5  # выплата доходит до счёта за 0–5 дней (как при загрузке выписки, 3.11)
BANK_DIFF_MIN = Decimal(1)  # копейки округления — не расхождение (как аномалия 7.1)

OK, DIFF, NO_BANK, WAITING, BANK_ONLY, NO_STATEMENTS = "ok", "diff", "no_bank", "waiting", "bank_only", "no_statements"
RED, YELLOW = "red", "yellow"


@dataclass(frozen=True)
class Payout:
    """Выплата по данным Ozon: день, сумма; по графику — сколько ожидали и за какую неделю продаж."""

    day: date
    amount: Decimal
    expected: Decimal | None = None
    week_from: date | None = None


@dataclass(frozen=True)
class Receipt:
    """Поступление от Ozon в выписке банка; payout_day — выплата, с которой его связала загрузка выписки."""

    id: str
    day: date
    amount: Decimal
    payout_day: date | None = None
    account_id: str | None = None
    op_id: str | None = None


@dataclass
class Row:
    status: str
    payout: Payout | None = None
    receipt: Receipt | None = None
    linked: bool = False  # пару связала загрузка выписки (иначе — найдена сверкой)

    @property
    def day(self) -> date:
        return self.payout.day if self.payout else self.receipt.day  # type: ignore[union-attr]

    @property
    def diff(self) -> Decimal | None:
        """Пришло на счёт − выплатил Ozon."""
        if self.payout is None or self.receipt is None:
            return None
        return (self.receipt.amount - self.payout.amount).quantize(CENT)


def reconcile(payouts: list[Payout], receipts: list[Receipt], bank_until: date | None, has_bank: bool,
              window: int = WINDOW_DAYS, bank_from: date | None = None) -> list[Row]:
    """Строки сверки: по выплате — её поступление и статус; поступления без выплаты — своими строками. bank_from …
    bank_until — за какие дни загружены выписки (выплата раньше первой выписки — «нет выписки», а не «не пришло»);
    has_bank — есть ли выписки вообще. Порядок — по дню, новые сверху."""
    rows: list[Row] = []
    left = list(receipts)
    by_day = {p.day: p for p in payouts}
    paired: dict[date, Row] = {}
    for r in sorted(receipts, key=lambda r: (r.day, r.id)):
        if r.payout_day is not None and r.payout_day in by_day and r.payout_day not in paired:
            paired[r.payout_day] = Row(OK, by_day[r.payout_day], r, linked=True)
            left.remove(r)
    for p in sorted(payouts, key=lambda p: p.day):
        if p.day in paired:
            continue
        near = [r for r in left if p.day <= r.day <= p.day + timedelta(days=window)]
        if near:
            r = min(near, key=lambda r: (abs(r.amount - p.amount), r.day, r.id))
            left.remove(r)
            paired[p.day] = Row(OK, p, r)
            continue
        if not has_bank:
            status = NO_STATEMENTS
        elif bank_until is not None and bank_until >= p.day + timedelta(days=window) and (
                bank_from is None or bank_from <= p.day):
            status = NO_BANK
        else:
            status = WAITING
        paired[p.day] = Row(status, p)
    for row in paired.values():
        if row.receipt is not None:
            row.status = OK if row.diff == 0 else DIFF
        rows.append(row)
    rows.extend(Row(BANK_ONLY, receipt=r) for r in left)
    rows.sort(key=lambda r: (r.day, r.payout is not None), reverse=True)
    return rows


@dataclass
class Issue:
    kind: str  # bank_missing | bank_diff | bank_only | schedule_diff | overdue | ozon_report
    severity: str  # red | yellow
    day: date
    title: str
    text: str  # что случилось и что сделать
    amount: Decimal | None = None  # деньги под вопросом: недополучено, не дошло, лишнее
    payout_day: date | None = None
    bank_day: date | None = None
    receipt: Receipt | None = None
    week_from: date | None = None
    extra: dict = field(default_factory=dict)


def _rub(v: Decimal) -> str:
    q = Decimal(v).quantize(CENT, ROUND_HALF_UP)
    whole, frac = f"{abs(q):.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{'−' if q < 0 else ''}{' '.join(groups)},{frac} ₽"


def _d(v: date) -> str:
    return v.strftime("%d.%m.%Y")


def _period_end(period: str) -> date:
    """«01.10.2026–15.10.2026» (сверка синхронизации баланса) → 15.10.2026."""
    d, m, y = period.split("–")[-1].strip().split(".")
    return date(int(y), int(m), int(d))


def issues(rows: list[Row], overdue: dict[date, Decimal | None], diff_pct: Decimal, reliable: bool,
           mismatches: list[dict] | None = None) -> list[Issue]:
    """Расхождения: красные (не пришло, просрочка) — первыми, внутри — новые сверху. diff_pct — порог расхождения
    выплаты с графиком (порог аномалий пользователя); reliable — график надёжен (иначе с ним не сверяем, как 7.1)."""
    out: list[Issue] = []
    for r in rows:
        p, b = r.payout, r.receipt
        if r.status == NO_BANK and p is not None:
            out.append(Issue(
                "bank_missing", RED, p.day, f"Выплата {_d(p.day)} не пришла на счёт",
                f"Ozon выплатил {_rub(p.amount)} {_d(p.day)}, а в выписке за {_d(p.day)}–"
                f"{_d(p.day + timedelta(days=WINDOW_DAYS))} поступления от Ozon нет. Проверьте реквизиты в кабинете Ozon "
                "(«Финансы → Реквизиты») и выписку банка; если деньги пришли на другой счёт — загрузите его выписку.",
                amount=p.amount, payout_day=p.day,
            ))
        elif r.status == DIFF and p is not None and b is not None and abs(r.diff) >= BANK_DIFF_MIN:  # type: ignore[arg-type]
            less = r.diff < 0  # type: ignore[operator]
            out.append(Issue(
                "bank_diff", YELLOW, p.day,
                f"На счёт пришло на {_rub(abs(r.diff))} {'меньше' if less else 'больше'}",  # type: ignore[arg-type]
                f"Ozon выплатил {_rub(p.amount)} {_d(p.day)}, на счёт {_d(b.day)} пришло {_rub(b.amount)}. "
                + ("Возможные причины: комиссия банка за зачисление, выплата пришла частями, поступление — за другую "
                   "выплату. Сверьте с выпиской и кабинетом Ozon." if less else
                   "Возможно, в поступление вошли две выплаты или доплата Ozon — сверьте с кабинетом Ozon."),
                amount=-r.diff if less else None, payout_day=p.day, bank_day=b.day, receipt=b,  # type: ignore[operator]
            ))
        elif r.status == BANK_ONLY and b is not None:
            out.append(Issue(
                "bank_only", YELLOW, b.day, f"Поступление от Ozon {_d(b.day)} без выплаты в данных Ozon",
                f"На счёт пришло {_rub(b.amount)}, а в балансе Ozon выплаты за эти дни нет: баланс за эти дни не "
                "загружен (обновите его в окне «Синхронизация») или это платёж по другому договору с Ozon.",
                bank_day=b.day, receipt=b,
            ))
        if p is not None and reliable and p.expected is not None and p.expected > 0:
            pct = (p.amount / p.expected - 1) * 100
            if abs(pct) > diff_pct:
                week = f"{_d(p.week_from)}–{_d(p.week_from + timedelta(days=6))}" if p.week_from else "неделю продаж"
                less = p.amount < p.expected
                out.append(Issue(
                    "schedule_diff", YELLOW, p.day,
                    f"Выплата {_d(p.day)} {'меньше' if less else 'больше'} ожидаемой на "
                    f"{abs(pct).quantize(Decimal('0.1'), ROUND_HALF_UP)} %",
                    f"По графику за продажи {week} ожидали {_rub(p.expected)} (начислено за неделю минус комиссия "
                    f"графика), выплачено {_rub(p.amount)}. Причины: возвраты и удержания после недели продаж, перенос "
                    "части выплаты, удержание по требованию или другой график выплат — проверьте начисления недели.",
                    amount=p.expected - p.amount if less else None, payout_day=p.day, week_from=p.week_from,
                ))
    for day, amount in overdue.items():
        out.append(Issue(
            "overdue", RED, day, f"Ожидалась выплата {_d(day)}, её нет",
            "По графику выплат Ozon должен был провести выплату"
            + (f" около {_rub(amount)}" if amount else "")
            + ", но в балансе её нет. Проверьте баланс и блокировки в кабинете Ozon («Финансы → Баланс»).",
            amount=amount, payout_day=day,
        ))
    for m in mismatches or []:
        out.append(Issue(
            "ozon_report", YELLOW, _period_end(m["period"]),
            f"Выплаты за {m['period']} расходятся с отчётом Ozon",
            f"По дням баланса выплачено {m['ours']} ₽, в финансовом отчёте Ozon за полмесяца — {m['ozon']} ₽. Сверьте с "
            "кабинетом Ozon («Финансы → Баланс»); обновите баланс в окне «Синхронизация».",
        ))
    out.sort(key=lambda i: (i.severity != RED, -i.day.toordinal()))
    return out
