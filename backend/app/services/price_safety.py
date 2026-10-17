"""Проверки безопасности изменения цен (шаг 6.1, BUSINESS_LOGIC §8). Здесь ничего не меняется и в Ozon не ходим:
проверяющий говорит, можно ли отправить новую цену, — errors блокируют, warnings нет.

Лимиты — константы модуля. Пользователь их не ослабляет: у PriceSafetyChecker нет других параметров, кроме режима
(manual | auto) и времени, настроек с лимитами нет.

Правила для товара (`PriceSafetyChecker.check`):
- новая цена и минимальная цена — больше 0, в рублях и копейках; что-то должно меняться;
- данные Ozon не аномальны: есть текущая цена (0 у Ozon = «не задана» — не с чем сравнить), комиссия < 90 %;
- изменение цены — не больше 20 % за раз (в ответе — диапазон, который можно сейчас);
- новая цена и новая минимальная цена — не ниже себестоимости с НДС × 1,05; себестоимости нет — нельзя (правило 5);
- минимальная цена ниже цены и не меньше 50 % от неё — иначе Ozon не примет (min_auto_price_too_small,
  price_less_than_min_auto_price); новая цена ниже цены до скидки (зачёркнутой) — иначе Ozon не примет
  (old_price_less_than_price); зачёркнутую сервис не меняет — решение владельца 17.10.2026, она меняется в кабинете
  Ozon;
- пауза между изменениями одного товара — час (считаются применённые и те, где Ozon не ответил);
- цена дальше 50 % от медианы за 30 дней (текущая — похоже на сбой данных, новая — цена «уехала» за несколько шагов):
  вручную — предупреждение, в автоправилах — блокировка (решение владельца 16.10.2026).
Откат (`PriceCheck.rollback`, решение владельца 16.10.2026): без паузы и лимита 20 % — но только ровно к прошлой цене,
изменению не больше 7 дней, оно дошло до Ozon, не само откат и ещё не откачено, и цена с тех пор не менялась.
Себестоимость × 1,05, мин. цена и аномалии данных проверяются и для отката.
Запрос целиком (`check_request`): не больше 500 товаров, без повторов; больше 10 — подтверждение, больше 50 — слово
«ПОДТВЕРЖДАЮ». В Ozon — батчами по 100 с паузой 2 с (OZON_BATCH, OZON_BATCH_PAUSE_SEC — шаг 6.3).
"""
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import products as engine
from app.engine.products import positive
from app.engine.tax import num, rub
from app.models.price_change import APPLIED, REACHED_OZON, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.schemas.sync import utc
from app.services.costs import load_cost_history
from app.services.sync.finance import MSK, today_msk
from app.services.sync.products import latest_prices
from app.services.tax_profile import supplier_vat_multiplier

# ── лимиты §8: жёсткие, пользователь не меняет ──
MAX_CHANGE_PCT = Decimal(20)  # изменение цены за раз, %
COST_FLOOR = Decimal("1.05")  # цена не ниже себестоимости с НДС × 1,05
PAUSE = timedelta(hours=1)  # между изменениями одного товара
CONFIRM_ABOVE = 10  # больше — подтверждение
CONFIRM_WORD_ABOVE = 50  # больше — ввести слово
CONFIRM_WORD = "ПОДТВЕРЖДАЮ"
MAX_PRODUCTS = 500  # товаров за один запрос
OZON_BATCH = 100  # товаров в одном запросе к Ozon
OZON_BATCH_PAUSE_SEC = 2  # пауза между батчами
ROLLBACK_MAX_AGE = timedelta(days=7)
ANOMALY_COMMISSION_PCT = Decimal(90)  # комиссия от 90 % — ошибка данных (шаг 4.3 — тот же порог)
MEDIAN_DAYS = 30
MEDIAN_MIN_DAYS = 7  # дней с известной ценой меньше — медиану не считаем (нет данных ≠ 0)
MEDIAN_DEVIATION_PCT = Decimal(50)
OZON_MIN_PRICE_SHARE = Decimal("0.5")  # Ozon: минимальная цена — не меньше 50 % цены

MANUAL, AUTO = "manual", "auto"
CONFIRM, WORD = "confirm", "word"  # какое подтверждение нужно запросу
CENT = Decimal("0.01")

NO_PRICE = (
    "Нет текущей цены товара из Ozon (цена 0 или ещё не загружалась) — не с чем сравнить изменение. "
    "Обновите товары и попробуйте снова."
)
NO_COST = (
    "Нет себестоимости товара — без неё не проверить, что цена не ниже себестоимости. "
    "Укажите её в карточке товара."
)


@dataclass(frozen=True)
class Rollback:
    """Изменение, которое откатываем, — что о нём известно."""

    change_id: uuid.UUID
    created_at: datetime
    applied: bool  # дошло до Ozon: применено или Ozon не ответил
    is_rollback: bool  # само — откат
    rolled_back: bool  # уже откатили
    price_before: Decimal | None
    price_after: Decimal | None
    min_price_before: Decimal | None = None
    min_price_after: Decimal | None = None


@dataclass(frozen=True)
class PriceCheck:
    """Предлагаемое изменение товара и факты, по которым его проверяем. Не меняющееся — None."""

    offer_id: str
    new_price: Decimal | None
    current_price: Decimal | None  # последний снимок Ozon; 0 — не задана
    new_min_price: Decimal | None = None
    current_min_price: Decimal | None = None
    current_old_price: Decimal | None = None  # цена до скидки (зачёркнутая); 0 — не задана
    cost_with_vat: Decimal | None = None  # себестоимость на сегодня × НДС поставщика; None — не задана
    commission_pct: Decimal | None = None
    median_price: Decimal | None = None  # медиана цены по дням за 30 дней; None — мало данных
    last_change_at: datetime | None = None  # последнее изменение через сервис, дошедшее до Ozon
    rollback: Rollback | None = None
    product_id: uuid.UUID | None = None


@dataclass
class SafetyResult:
    errors: list[str] = field(default_factory=list)  # блокируют
    warnings: list[str] = field(default_factory=list)  # нет
    floor: Decimal | None = None  # ниже нельзя: себестоимость с НДС × 1,05
    change_pct: Decimal | None = None  # на сколько меняется цена, %
    allowed_from: Decimal | None = None  # какую цену можно поставить за один раз (±20 % и не ниже floor)
    allowed_to: Decimal | None = None
    next_change_at: datetime | None = None  # пауза: раньше менять нельзя

    @property
    def ok(self) -> bool:
        return not self.errors


def cost_floor(cost_with_vat: Decimal) -> Decimal:
    return (cost_with_vat * COST_FLOOR).quantize(CENT, ROUND_HALF_UP)


def allowed_range(c: PriceCheck) -> tuple[Decimal | None, Decimal | None]:
    """Какую цену можно поставить за один раз по фактам товара: ±20 % от текущей, не ниже себестоимости с НДС × 1,05 и
    ниже зачёркнутой. Цены нет — (None, None)."""
    cur = positive(c.current_price)
    if cur is None:
        return None, None
    floor = cost_floor(c.cost_with_vat) if c.cost_with_vat is not None else None
    old = positive(c.current_old_price)
    return PriceSafetyChecker._range(cur, floor, old - CENT if old is not None else None)


def required_confirmation(count: int) -> str | None:
    """Что нужно от пользователя для запроса на count товаров: ничего, подтверждение или слово."""
    if count > CONFIRM_WORD_ABOVE:
        return WORD
    if count > CONFIRM_ABOVE:
        return CONFIRM
    return None


def _deviation(value: Decimal, median: Decimal) -> Decimal:
    return abs(value - median) / median * 100


def _pct(value: Decimal) -> str:
    q = value.quantize(Decimal("0.1"), ROUND_HALF_UP)
    return f"{'+' if q > 0 else ''}{num(q)} %"


def _msk(dt: datetime) -> datetime:
    return dt.astimezone(MSK)


class PriceSafetyChecker:
    """Проверки §8. mode — manual (человек подтверждает предпросмотр) или auto (правила, фаза 9)."""

    def __init__(self, mode: str = MANUAL, now: datetime | None = None) -> None:
        if mode not in (MANUAL, AUTO):
            raise ValueError(f"Неизвестный режим проверки: {mode}")
        self.mode = mode
        self.now = now or datetime.now(timezone.utc)

    def _anomaly(self, r: SafetyResult, text: str) -> None:
        """Подозрительные данные: человек увидит и решит сам, правило — не применит."""
        (r.errors if self.mode == AUTO else r.warnings).append(text)

    def check(self, c: PriceCheck) -> SafetyResult:
        r = SafetyResult()
        new, new_min = c.new_price, c.new_min_price
        if new is None and new_min is None:
            r.errors.append("Укажите новую цену или минимальную цену.")
            return r
        for value, name in ((new, "Новая цена"), (new_min, "Минимальная цена")):
            if value is None:
                continue
            if value <= 0:
                r.errors.append(f"{name} — число больше 0.")
            elif value != value.quantize(CENT):
                r.errors.append(f"{name} — в рублях и копейках: не больше двух знаков после запятой.")
        if r.errors:
            return r

        cur, cur_min = positive(c.current_price), positive(c.current_min_price)
        old = positive(c.current_old_price)
        ceiling = old - CENT if old is not None else None  # цена должна быть ниже зачёркнутой
        rb = c.rollback
        if cur is None:
            r.errors.append(NO_PRICE)
        if c.commission_pct is not None and c.commission_pct >= ANOMALY_COMMISSION_PCT:
            r.errors.append(
                f"Комиссия Ozon по товару {num(c.commission_pct)} % выглядит ошибкой данных — ничего не меняем, "
                "пока Ozon не отдаст нормальные данные. Обновите товары позже."
            )
        price_moves = new is not None and cur is not None and new != cur
        min_moves = new_min is not None and new_min != cur_min
        if cur is not None and not price_moves and not min_moves:
            r.errors.append(
                f"Цена уже {rub(cur)} — менять нечего." if new_min is None
                else "Цена и минимальная цена уже такие — менять нечего."
            )
        med = c.median_price
        if med and cur is not None and _deviation(cur, med) > MEDIAN_DEVIATION_PCT:
            self._anomaly(r, (
                f"Цена в Ozon сейчас {rub(cur)} — на {num(_deviation(cur, med).quantize(Decimal(1)))} % "
                f"дальше медианы за {MEDIAN_DAYS} дней ({rub(med)}). Похоже на сбой данных или резкое изменение в "
                "кабинете Ozon — проверьте цену."
            ))

        if (price_moves or min_moves) and c.cost_with_vat is None:
            r.errors.append(NO_COST)
        elif price_moves or min_moves:
            r.floor = cost_floor(c.cost_with_vat)
        if price_moves:
            pct = (new - cur) / cur * 100
            r.change_pct = pct.quantize(Decimal("0.1"), ROUND_HALF_UP)
            if rb is None and abs(pct) > MAX_CHANGE_PCT:
                r.errors.append(
                    f"Изменение на {_pct(pct)} — больше {num(MAX_CHANGE_PCT)} % за раз (защита от опечаток). "
                    + self._range_text(cur, r.floor, ceiling)
                )
            if r.floor is not None and new < r.floor:
                r.errors.append(
                    f"Цена ниже себестоимости с НДС + 5 % ({rub(r.floor)}) — так нельзя, это защита от ошибки."
                )
            if old is not None and new >= old:
                r.errors.append(
                    f"Новая цена {rub(new)} не ниже цены до скидки ({rub(old)}, её покупатель видит зачёркнутой) — "
                    f"Ozon такую не примет. Поставьте цену ниже {rub(old)} или сначала измените цену до скидки в "
                    "кабинете Ozon."
                )
            if med and _deviation(new, med) > MEDIAN_DEVIATION_PCT:
                self._anomaly(r, (
                    f"Новая цена {rub(new)} — на {num(_deviation(new, med).quantize(Decimal(1)))} % дальше медианы "
                    f"за {MEDIAN_DAYS} дней ({rub(med)}). Проверьте, что так и задумано."
                ))
        if min_moves and r.floor is not None and new_min < r.floor:
            r.errors.append(
                f"Минимальная цена ниже себестоимости с НДС + 5 % ({rub(r.floor)}): Ozon сможет опускать цену в "
                "акциях ниже неё — так нельзя."
            )
        price = new if new is not None else cur
        min_price = new_min if new_min is not None else cur_min
        if (price_moves or min_moves) and price is not None and min_price is not None:
            if min_price >= price:
                r.errors.append(
                    f"Минимальная цена ({rub(min_price)}) должна быть ниже цены ({rub(price)}) — иначе Ozon не "
                    "примет." + ("" if min_moves else " Снизьте и минимальную цену.")
                )
            elif min_price < price * OZON_MIN_PRICE_SHARE:
                r.errors.append(
                    f"Минимальная цена ({rub(min_price)}) меньше половины цены ({rub(price)}) — Ozon такую не "
                    "примет."
                )

        if rb is None and c.last_change_at is not None and self.now < c.last_change_at + PAUSE:
            r.next_change_at = c.last_change_at + PAUSE
            minutes = max(int((self.now - c.last_change_at).total_seconds() // 60), 0)
            r.errors.append(
                f"Цену этого товара меняли {minutes} мин. назад — следующее изменение не раньше "
                f"{_msk(r.next_change_at):%H:%M} по Москве (через час после прошлого, защита от ошибок)."
            )
        if rb is not None:
            self._rollback(r, c, cur, cur_min)
        elif cur is not None:
            r.allowed_from, r.allowed_to = self._range(cur, r.floor, ceiling)
        return r

    @staticmethod
    def _range(
        cur: Decimal, floor: Decimal | None, ceiling: Decimal | None = None
    ) -> tuple[Decimal | None, Decimal | None]:
        """Какую цену можно поставить за один раз: ±20 %, не ниже floor и не выше ceiling."""
        share = MAX_CHANGE_PCT / 100
        lo = (cur * (1 - share)).quantize(CENT, ROUND_CEILING)
        hi = (cur * (1 + share)).quantize(CENT, ROUND_FLOOR)
        if floor is not None:
            lo = max(lo, floor)
        if ceiling is not None:
            hi = min(hi, ceiling)
        return (lo, hi) if lo <= hi else (None, None)

    def _range_text(self, cur: Decimal, floor: Decimal | None, ceiling: Decimal | None) -> str:
        lo, hi = self._range(cur, floor, ceiling)
        if lo is None:
            return (
                "Сейчас цену за один раз не поменять: в пределах ±20 % нет цены не ниже себестоимости с НДС + 5 %"
                + (" и ниже цены до скидки." if ceiling is not None else ".")
            )
        return f"За один раз сейчас можно от {rub(lo)} до {rub(hi)}; дальше — следующим шагом, не раньше чем через час."

    def _rollback(self, r: SafetyResult, c: PriceCheck, cur: Decimal | None, cur_min: Decimal | None) -> None:
        rb = c.rollback
        if not rb.applied:
            r.errors.append("Это изменение не дошло до Ozon — откатывать нечего.")
        if rb.is_rollback:
            r.errors.append(
                "Это изменение — уже откат, а откат отката запрещён. Нужна другая цена — измените её обычным способом."
            )
        if rb.rolled_back:
            r.errors.append("Это изменение уже откатили.")
        if self.now - rb.created_at > ROLLBACK_MAX_AGE:
            r.errors.append(
                f"Откатить можно изменение не старше {ROLLBACK_MAX_AGE.days} дней, а это — от "
                f"{_msk(rb.created_at):%d.%m.%Y}. Измените цену обычным способом."
            )
        if rb.price_after is not None and cur is not None and cur != rb.price_after:
            r.errors.append(
                f"После этого изменения цена в Ozon уже другая: сейчас {rub(cur)}, а изменение ставило "
                f"{rub(rb.price_after)}. Откат вернул бы не ту цену — измените её обычным способом."
            )
        if rb.min_price_after is not None and cur_min != rb.min_price_after:
            r.errors.append(
                "После этого изменения минимальная цена в Ozon уже другая — откат вернул бы не ту. "
                "Измените её обычным способом."
            )
        if rb.price_after is not None and c.new_price != rb.price_before:
            r.errors.append(f"Откат возвращает ровно прошлую цену — {rub(rb.price_before)}.")
        if rb.min_price_after is not None and c.new_min_price != rb.min_price_before:
            r.errors.append("Откат возвращает ровно прошлую минимальную цену.")
        # откат меняет только то, что меняло изменение, — иначе через него прошла бы любая цена без лимита 20 %
        if rb.price_after is None and c.new_price is not None and c.new_price != cur:
            r.errors.append("Это изменение не трогало цену — откат её не меняет.")
        if rb.min_price_after is None and c.new_min_price is not None and c.new_min_price != cur_min:
            r.errors.append("Это изменение не трогало минимальную цену — откат её не меняет.")

    def check_request(
        self, offer_ids: Sequence[str], confirmed: bool = False, confirm_word: str | None = None
    ) -> SafetyResult:
        """Запрос целиком: сколько товаров, повторы, подтверждение."""
        r = SafetyResult()
        n = len(offer_ids)
        if n == 0:
            r.errors.append("Не выбрано ни одного товара.")
            return r
        if n > MAX_PRODUCTS:
            r.errors.append(f"За один раз — не больше {MAX_PRODUCTS} товаров, выбрано {n}. Разделите на части.")
        repeated = sorted(o for o, k in Counter(offer_ids).items() if k > 1)
        if repeated:
            r.errors.append(f"Товар повторяется в запросе: {', '.join(repeated)} — оставьте по одной строке.")
        need = required_confirmation(n)
        if need == CONFIRM and not confirmed:
            r.errors.append(f"Меняются цены {n} товаров — больше {CONFIRM_ABOVE}: подтвердите изменение.")
        if need == WORD and (confirm_word or "").strip().upper() != CONFIRM_WORD:
            r.errors.append(
                f"Меняются цены {n} товаров — больше {CONFIRM_WORD_ABOVE}: для подтверждения введите слово "
                f"«{CONFIRM_WORD}»."
            )
        return r


# ── факты для проверки из БД ──
@dataclass(frozen=True)
class Proposal:
    product: Product
    new_price: Decimal | None = None
    new_min_price: Decimal | None = None
    rollback: Rollback | None = None


async def median_prices(
    db: AsyncSession, product_ids: Sequence[uuid.UUID], today: date | None = None
) -> dict[uuid.UUID, Decimal]:
    """Медиана цены по дням (цена на конец дня по Москве) за 30 дней по сегодня; меньше 7 дней с ценой — нет."""
    if not product_ids:
        return {}
    today = today or today_msk()
    date_from = today - timedelta(days=MEDIAN_DAYS - 1)
    start = datetime.combine(date_from, time(), MSK).astimezone(timezone.utc)
    inside = (await db.execute(
        select(ProductPrice).where(ProductPrice.product_id.in_(product_ids), ProductPrice.snapshot_at >= start)
    )).scalars().all()
    last_before = (
        select(ProductPrice.product_id, func.max(ProductPrice.snapshot_at).label("mx"))
        .where(ProductPrice.product_id.in_(product_ids), ProductPrice.snapshot_at < start)
        .group_by(ProductPrice.product_id)
        .subquery()
    )
    before = (await db.execute(select(ProductPrice).join(last_before, and_(
        ProductPrice.product_id == last_before.c.product_id, ProductPrice.snapshot_at == last_before.c.mx,
    )))).scalars().all()
    points: dict[uuid.UUID, list[engine.PricePoint]] = defaultdict(list)
    for s in sorted([*before, *inside], key=lambda s: utc(s.snapshot_at)):
        points[s.product_id].append(engine.PricePoint(utc(s.snapshot_at).astimezone(MSK).date(), s.price))
    out: dict[uuid.UUID, Decimal] = {}
    for pid, pts in points.items():
        prices = [d["price"] for d in engine.daily_prices(pts, date_from, today) if d["price"] is not None]
        if len(prices) >= MEDIAN_MIN_DAYS:
            out[pid] = Decimal(statistics.median(prices)).quantize(CENT, ROUND_HALF_UP)
    return out


async def last_changes(
    db: AsyncSession, product_ids: Sequence[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, datetime]:
    """Последнее изменение цены через сервис за час — если оно дошло до Ozon (применено, Ozon не ответил или ждёт
    очереди в массовом изменении)."""
    if not product_ids:
        return {}
    since = (now or datetime.now(timezone.utc)) - PAUSE
    rows = await db.execute(
        select(PriceChange.product_id, func.max(PriceChange.created_at))
        .where(
            PriceChange.product_id.in_(product_ids), PriceChange.status.in_(REACHED_OZON),
            PriceChange.created_at >= since,
        )
        .group_by(PriceChange.product_id)
    )
    return {pid: utc(at) for pid, at in rows.all()}


async def rollback_info(db: AsyncSession, change: PriceChange) -> Rollback:
    rolled = await db.scalar(
        select(func.count()).select_from(PriceChange)
        .where(PriceChange.rollback_of_id == change.id, PriceChange.status.in_((APPLIED, UNKNOWN)))
    )
    return Rollback(
        change_id=change.id, created_at=utc(change.created_at), applied=change.status in (APPLIED, UNKNOWN),
        is_rollback=change.rollback_of_id is not None, rolled_back=bool(rolled),
        price_before=change.price_before, price_after=change.price_after,
        min_price_before=change.min_price_before, min_price_after=change.min_price_after,
    )


async def gather(
    db: AsyncSession, user_id: uuid.UUID, proposals: Sequence[Proposal], now: datetime | None = None
) -> list[PriceCheck]:
    """Факты для проверки: последний снимок цен Ozon, себестоимость на сегодня с НДС (НДС записи или налогового
    профиля — как в калькуляторе 4.2), медиана за 30 дней, последнее изменение через сервис."""
    ids = [p.product.id for p in proposals]
    today = today_msk()
    snaps = await latest_prices(db, ids)
    history = await load_cost_history(db, user_id)
    default_vat = await supplier_vat_multiplier(db, user_id, today)
    medians = await median_prices(db, ids, today)
    last = await last_changes(db, ids, now)
    out = []
    for p in proposals:
        snap = snaps.get(p.product.id)
        record = history.record_at(p.product.offer_id, today)
        cost = None
        if record is not None:
            vat = Decimal(record.vat_multiplier) if record.vat_multiplier is not None else default_vat
            cost = Decimal(record.cost_price) * vat
        out.append(PriceCheck(
            offer_id=p.product.offer_id, new_price=p.new_price, new_min_price=p.new_min_price,
            current_price=snap.price if snap else None, current_min_price=snap.min_price if snap else None,
            current_old_price=snap.old_price if snap else None,
            cost_with_vat=cost, commission_pct=snap.commission_pct if snap else None,
            median_price=medians.get(p.product.id), last_change_at=last.get(p.product.id), rollback=p.rollback,
            product_id=p.product.id,
        ))
    return out


async def check(
    db: AsyncSession, user_id: uuid.UUID, proposals: Sequence[Proposal], mode: str = MANUAL,
    now: datetime | None = None,
) -> list[tuple[PriceCheck, SafetyResult]]:
    """Проверить каждое изменение по данным из БД."""
    checker = PriceSafetyChecker(mode, now)
    return [(c, checker.check(c)) for c in await gather(db, user_id, proposals, checker.now)]
