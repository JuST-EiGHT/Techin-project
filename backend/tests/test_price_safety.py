"""Проверки безопасности изменения цен (шаг 6.1, BUSINESS_LOGIC §8): каждое правило, откат, запрос целиком и факты
из БД (снимки цен, себестоимость, медиана, пауза)."""
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.models.price_change import APPLIED, BLOCKED, MANUAL_SINGLE, UNKNOWN, PriceChange
from app.models.product import Product, ProductPrice
from app.models.product_cost import BEGINNING, ProductCost
from app.services import price_safety as ps
from app.services.price_safety import AUTO, MANUAL, PriceCheck, PriceSafetyChecker, Rollback
from app.services.sync.finance import MSK
from tests.test_pricing import user_id_of

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)  # 12:00 по Москве
COST = D("200.08")  # 164 ₽ × 1,22 → не ниже 210,08 ₽


def item(**kw) -> PriceCheck:
    """Товар по 1000 ₽, себестоимость с НДС 200,08 ₽, комиссия 38 %, медиана за 30 дней — 1000 ₽."""
    base = dict(offer_id="ART-1", new_price=D("1100"), current_price=D("1000"), cost_with_vat=COST,
                commission_pct=D("38"), median_price=D("1000"))
    return PriceCheck(**{**base, **kw})


def check(mode=MANUAL, **kw):
    return PriceSafetyChecker(mode, NOW).check(item(**kw))


def has(texts: list[str], part: str) -> bool:
    return any(part in t for t in texts)


def test_limits_are_hard_constants():
    # §8: пользователь их не меняет — у проверяющего нет других параметров, кроме режима и времени
    assert (ps.MAX_CHANGE_PCT, ps.COST_FLOOR, ps.PAUSE) == (D(20), D("1.05"), timedelta(hours=1))
    assert (ps.CONFIRM_ABOVE, ps.CONFIRM_WORD_ABOVE, ps.CONFIRM_WORD, ps.MAX_PRODUCTS) == (10, 50, "ПОДТВЕРЖДАЮ", 500)
    assert (ps.OZON_BATCH, ps.OZON_BATCH_PAUSE_SEC, ps.ROLLBACK_MAX_AGE) == (100, 2, timedelta(days=7))
    assert (ps.ANOMALY_COMMISSION_PCT, ps.MEDIAN_DAYS, ps.MEDIAN_DEVIATION_PCT) == (D(90), 30, D(50))
    assert list(inspect.signature(PriceSafetyChecker).parameters) == ["mode", "now"]
    with pytest.raises(ValueError):
        PriceSafetyChecker("loose")
    # вход в акции (4.3) — те же лимиты
    from app.services import promotion_actions
    assert (promotion_actions.COST_FLOOR, promotion_actions.PAUSE, promotion_actions.SUSPICIOUS_COMMISSION_PCT) == (
        ps.COST_FLOOR, ps.PAUSE, ps.ANOMALY_COMMISSION_PCT)


def test_change_within_limits():
    r = check(new_price=D("1150"))
    assert r.ok and r.warnings == []
    assert (r.change_pct, r.floor, r.allowed_from, r.allowed_to) == (D("15.0"), D("210.08"), D("800.00"), D("1200.00"))


def test_change_not_more_than_20_pct():
    assert check(new_price=D("1200")).ok and check(new_price=D("800")).ok  # ровно 20 % — можно
    up = check(new_price=D("1250"))
    assert not up.ok and has(up.errors, "Изменение на +25 % — больше 20 % за раз")
    assert has(up.errors, "от 800,00 ₽ до 1 200,00 ₽")
    down = check(new_price=D("700"))
    assert has(down.errors, "Изменение на -30 %") and down.change_pct == D("-30.0")
    assert not check(new_price=D("1200.01")).ok


def test_not_below_cost_with_vat_plus_5_pct():
    r = check(current_price=D("250"), new_price=D("210.08"))  # ровно себестоимость с НДС × 1,05
    assert r.ok and (r.allowed_from, r.allowed_to) == (D("210.08"), D("300.00"))  # −20 % = 200 — ниже нельзя
    r = check(current_price=D("250"), new_price=D("210.07"))
    assert has(r.errors, "ниже себестоимости с НДС + 5 % (210,08 ₽)")
    # себестоимости нет — не проверить, значит нельзя (правило 5)
    r = check(cost_with_vat=None)
    assert not r.ok and has(r.errors, "Нет себестоимости")
    # даже +20 % ниже себестоимости — диапазона нет
    r = check(current_price=D("150"), new_price=D("180"))
    assert has(r.errors, "ниже себестоимости") and r.allowed_from is None


def test_pause_one_hour_between_changes():
    r = check(last_change_at=NOW - timedelta(minutes=30))
    assert has(r.errors, "меняли 30 мин. назад") and has(r.errors, "не раньше 12:30 по Москве")
    assert r.next_change_at == NOW + timedelta(minutes=30)
    assert check(last_change_at=NOW - timedelta(minutes=61)).ok


def test_ozon_data_anomalies():
    for cur in (D(0), None):  # цена 0 у Ozon = не задана: не с чем сравнить
        assert has(check(current_price=cur).errors, "Нет текущей цены товара из Ozon")
    assert has(check(commission_pct=D("90")).errors, "Комиссия Ozon по товару 90 % выглядит ошибкой")
    assert check(commission_pct=D("89.9")).ok
    # текущая цена дальше 50 % от медианы за 30 дней: вручную — предупреждение, в автоправилах — блокировка
    manual = check(current_price=D("1000"), median_price=D("600"), new_price=D("1050"))
    assert manual.ok and has(manual.warnings, "на 67 % дальше медианы за 30 дней (600,00 ₽)")
    auto = check(AUTO, current_price=D("1000"), median_price=D("600"), new_price=D("1050"))
    assert not auto.ok and has(auto.errors, "дальше медианы")
    # новая цена «уехала» от медианы за несколько шагов по 20 %
    manual = check(current_price=D("1400"), new_price=D("1600"))
    assert manual.ok and has(manual.warnings, "Новая цена 1 600,00 ₽ — на 60 % дальше медианы")
    assert not check(AUTO, current_price=D("1400"), new_price=D("1600")).ok
    # медианы нет (мало истории) — не проверяем
    assert check(current_price=D("1400"), new_price=D("1600"), median_price=None).warnings == []


def test_values_and_nothing_to_change():
    assert check(new_price=None).errors == ["Укажите новую цену или минимальную цену."]
    assert has(check(new_price=D(0)).errors, "Новая цена — число больше 0")
    assert has(check(new_price=D("-5")).errors, "больше 0")
    assert has(check(new_price=D("1000.001")).errors, "не больше двух знаков")
    assert check(new_price=D("1000")).errors == ["Цена уже 1 000,00 ₽ — менять нечего."]


def test_min_price():
    assert check(new_price=None, new_min_price=D("800")).ok  # меняется только минимальная
    assert has(check(new_price=None, new_min_price=D("1000")).errors, "должна быть ниже цены")
    assert has(check(new_price=None, new_min_price=D("400")).errors, "меньше половины цены")
    r = check(current_price=D("300"), new_price=None, new_min_price=D("200"))
    assert has(r.errors, "Минимальная цена ниже себестоимости с НДС + 5 %")
    # новая цена ниже уже стоящей в Ozon минимальной — Ozon не примет
    r = check(current_min_price=D("900"), new_price=D("850"))
    assert has(r.errors, "Минимальная цена (900,00 ₽) должна быть ниже цены (850,00 ₽)") and has(r.errors, "Снизьте")
    assert check(current_min_price=D("900"), new_price=D("850"), new_min_price=D("800")).ok
    assert check(current_min_price=D(0), new_price=D("850")).ok  # 0 = мин. цена не задана


def test_below_old_price():
    # Ozon не принимает цену не ниже зачёркнутой (old_price_less_than_price); зачёркнутую сервис не меняет
    r = check(current_old_price=D("1150"), new_price=D("1150"))
    assert has(r.errors, "не ниже цены до скидки (1 150,00 ₽") and has(r.errors, "в кабинете Ozon")
    r = check(current_old_price=D("1150"), new_price=D("1100"))
    assert r.ok and r.allowed_to == D("1149.99")  # диапазон «за один раз» — ниже зачёркнутой
    assert check(current_old_price=D(0), new_price=D("1150")).ok  # 0 — зачёркнутой нет
    r = check(current_old_price=D("1150"), new_price=D("1250"))
    assert has(r.errors, "от 800,00 ₽ до 1 149,99 ₽")
    r = check(current_old_price=D("700"), new_price=D("900"), current_price=D("690"), cost_with_vat=D("700"))
    assert has(r.errors, "нет цены не ниже себестоимости с НДС + 5 % и ниже цены до скидки")


def rollback(**kw) -> Rollback:
    """Изменение 1000 → 800 ₽ (−20 %) десять минут назад."""
    base = dict(change_id=uuid.uuid4(), created_at=NOW - timedelta(minutes=10), applied=True, is_rollback=False,
                rolled_back=False, price_before=D("1000"), price_after=D("800"))
    return Rollback(**{**base, **kw})


def test_rollback_skips_pause_and_20_pct():
    # +25 % и пауза ещё идёт — для отката можно (решение владельца): ровно к прошлой цене
    r = check(current_price=D("800"), new_price=D("1000"), last_change_at=NOW - timedelta(minutes=10),
              rollback=rollback())
    assert r.ok, r.errors
    assert r.change_pct == D("25.0") and r.next_change_at is None


@pytest.mark.parametrize("kw, cur, new, error", [
    (dict(created_at=NOW - timedelta(days=8)), "800", "1000", "не старше 7 дней, а это — от 08.10.2026"),
    (dict(is_rollback=True), "800", "1000", "откат отката запрещён"),
    (dict(rolled_back=True), "800", "1000", "уже откатили"),
    (dict(applied=False), "800", "1000", "не дошло до Ozon"),
    ({}, "850", "1000", "сейчас 850,00 ₽, а изменение ставило 800,00 ₽"),
    ({}, "800", "950", "Откат возвращает ровно прошлую цену — 1 000,00 ₽"),
    # изменение меняло только мин. цену — через его откат цену не провезти
    (dict(price_before=None, price_after=None, min_price_before=D("700"), min_price_after=D("600")), "800", "1000",
     "не трогало цену"),
])
def test_rollback_rules(kw, cur, new, error):
    r = check(current_price=D(cur), new_price=D(new), rollback=rollback(**kw))
    assert not r.ok and has(r.errors, error), r.errors


def test_rollback_of_min_price_only():
    rb = rollback(price_before=None, price_after=None, min_price_before=D("700"), min_price_after=D("600"))
    assert check(current_min_price=D("600"), new_price=None, new_min_price=D("700"), rollback=rb).ok


def test_rollback_still_checks_cost_floor():
    # себестоимость с тех пор выросла: 1000 ₽ ниже себестоимости с НДС × 1,05 — откат нельзя
    r = check(current_price=D("800"), new_price=D("1000"), cost_with_vat=D("1000"), rollback=rollback())
    assert has(r.errors, "ниже себестоимости с НДС + 5 % (1 050,00 ₽)")


def test_request_limits_and_confirmation():
    checker = PriceSafetyChecker(MANUAL, NOW)
    offers = lambda n: [f"ART-{i}" for i in range(n)]  # noqa: E731
    assert ps.required_confirmation(10) is None and ps.required_confirmation(11) == ps.CONFIRM
    assert ps.required_confirmation(50) == ps.CONFIRM and ps.required_confirmation(51) == ps.WORD
    assert checker.check_request(offers(10)).ok
    assert has(checker.check_request(offers(11)).errors, "больше 10: подтвердите")
    assert checker.check_request(offers(11), confirmed=True).ok
    r = checker.check_request(offers(51), confirmed=True)
    assert has(r.errors, "введите слово «ПОДТВЕРЖДАЮ»")
    assert not checker.check_request(offers(51), confirm_word="да").ok
    assert checker.check_request(offers(51), confirm_word=" подтверждаю ").ok
    assert checker.check_request(offers(500), confirm_word="ПОДТВЕРЖДАЮ").ok
    assert has(checker.check_request(offers(501), confirm_word="ПОДТВЕРЖДАЮ").errors, "не больше 500 товаров, выбрано 501")
    assert has(checker.check_request(["A", "B", "A"]).errors, "повторяется в запросе: A")
    assert checker.check_request([]).errors == ["Не выбрано ни одного товара."]


# ── факты из БД ──
def at(days_ago: int, now: datetime) -> datetime:
    return now - timedelta(days=days_ago)


async def test_gather_from_db(client, tokens, sessions):
    user_id = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        products = []
        for n in (1, 2, 3):
            p = Product(user_id=user_id, ozon_product_id=n, offer_id=f"ART-{n}", name=f"Товар {n}", synced_at=now)
            db.add(p)
            products.append(p)
        await db.flush()
        a, b, c = products
        # A: 40 дней по 1000 ₽, сегодня — 1600 ₽ (медиана за 30 дней — 1000 ₽); себестоимость 164 ₽ без своей ставки НДС
        db.add(ProductPrice(product_id=a.id, snapshot_at=at(40, now), price=D("1000"), min_price=D(0),
                            commission_pct=D("38")))
        db.add(ProductPrice(product_id=a.id, snapshot_at=now - timedelta(minutes=5), price=D("1600"),
                            min_price=D("900"), commission_pct=D("38")))
        db.add(ProductCost(user_id=user_id, offer_id="ART-1", cost_price=D("164"), vat_multiplier=None,
                           effective_from=BEGINNING, source="manual", created_at=now))
        # B: цена известна только 3 дня — медианы нет; себестоимость со своей ставкой
        db.add(ProductPrice(product_id=b.id, snapshot_at=at(2, now), price=D("500"), commission_pct=D("95")))
        db.add(ProductCost(user_id=user_id, offer_id="ART-2", cost_price=D("100"), vat_multiplier=D("1.1"),
                           effective_from=BEGINNING, source="manual", created_at=now))
        # C: цен из Ozon нет совсем, себестоимости нет
        # изменения через сервис: A — применено 20 мин назад; заблокированное позже не считается;
        # B — Ozon не ответил 10 мин назад (могло примениться) — считается; C — применено 2 часа назад — пауза прошла
        def change(product, minutes, status, **kw):
            row = PriceChange(user_id=user_id, product_id=product.id, offer_id=product.offer_id,
                              created_at=now - timedelta(minutes=minutes), change_type=MANUAL_SINGLE, status=status,
                              is_applied=status == APPLIED, price_before=D("1000"), price_after=D("1100"), **kw)
            db.add(row)
            return row
        applied = change(a, 20, APPLIED)
        change(a, 5, BLOCKED)
        change(b, 10, UNKNOWN)
        change(c, 120, APPLIED)
        await db.flush()
        undo = change(a, 1, APPLIED, rollback_of_id=applied.id)
        await db.commit()

        checks = await ps.gather(db, user_id, [ps.Proposal(a, D("1700")), ps.Proposal(b, D("550")),
                                               ps.Proposal(c, D("100"))])
        ca, cb, cc = checks
        assert (ca.current_price, ca.current_min_price, ca.commission_pct) == (D("1600"), D("900"), D("38"))
        assert ca.cost_with_vat == D("164") * D("1.22")  # ставка не задана — НДС поставщика по умолчанию (22 %)
        assert ca.median_price == D("1000.00") and ca.product_id == a.id
        assert abs(ca.last_change_at - (now - timedelta(minutes=1))) < timedelta(seconds=1)  # откат — тоже изменение
        assert cb.median_price is None and cb.cost_with_vat == D("110.0")
        assert abs(cb.last_change_at - (now - timedelta(minutes=10))) < timedelta(seconds=1)
        assert (cc.current_price, cc.cost_with_vat, cc.last_change_at) == (None, None, None)

        results = await ps.check(db, user_id, [ps.Proposal(a, D("1700")), ps.Proposal(b, D("550"))])
        ra, rb = (r for _, r in results)
        assert has(ra.errors, "меняли 1 мин. назад") and has(ra.warnings, "дальше медианы")
        assert has(rb.errors, "Комиссия Ozon по товару 95 %") and has(rb.errors, "меняли 10 мин. назад")

        info = await ps.rollback_info(db, applied)
        assert (info.applied, info.is_rollback, info.rolled_back) == (True, False, True)
        info = await ps.rollback_info(db, undo)
        assert (info.is_rollback, info.rolled_back, info.price_before) == (True, False, D("1000"))


async def test_median_needs_seven_days(client, tokens, sessions):
    user_id = await user_id_of(client, tokens)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        p = Product(user_id=user_id, ozon_product_id=7, offer_id="ART-7", synced_at=now)
        db.add(p)
        await db.flush()
        # цена есть с 6 дней назад по Москве — сегодня 7-й день: медиана есть; 0 у Ozon в днях не считается
        first = datetime.combine((now.astimezone(MSK) - timedelta(days=6)).date(), datetime.min.time(), MSK)
        db.add(ProductPrice(product_id=p.id, snapshot_at=first + timedelta(hours=1), price=D("700")))
        db.add(ProductPrice(product_id=p.id, snapshot_at=first + timedelta(days=1, hours=1), price=D("900")))
        await db.commit()
        assert (await ps.median_prices(db, [p.id])) == {p.id: D("900.00")}
        db.add(ProductPrice(product_id=p.id, snapshot_at=first + timedelta(days=2, hours=1), price=D(0)))
        await db.commit()
        # 0 — «цена не задана», такие дни выпадают: осталось 2 дня с ценой — медианы нет
        assert await ps.median_prices(db, [p.id]) == {}
