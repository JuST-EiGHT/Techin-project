"""Пороги предупреждений по разделам уведомлений (шаг 10.25). Значения — те же поля настроек магазина, что и раньше
(`user_settings`, шаг 7.1): с 10.25 их меняют в «Аккаунт → Уведомления» у своего раздела, а не в «Настройках»; ничего
не переносится и не теряется. Проверки — `user_settings.update_settings` (жёлтый меньше красного, границы)."""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import anomaly_detector as det
from app.models.user_settings import UserSettings
from app.services import user_settings as svc


@dataclass(frozen=True)
class Threshold:
    key: str  # поле user_settings
    section: str  # раздел уведомлений (engine/notifications.SECTIONS)
    label: str
    unit: str
    why: str
    min: Decimal
    max: Decimal
    places: int = 0


D = Decimal
THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("anomaly_sales_drop_pct", "sales", "Упали больше чем на", "%", "Жёлтая — проверьте цену, остатки, акции и "
              "карточку товара.", D(1), D(99)),
    Threshold("anomaly_sales_drop_red_pct", "sales", "Упали — красная, больше", "%", "Красная — продажи почти встали.",
              D(1), D(99)),
    Threshold("anomaly_sales_growth_pct", "sales", "Выросли больше чем на", "%", "Резкий рост — повод проверить, не "
              "слишком ли низкая цена.", D(10), D(1000)),
    Threshold("low_margin_pct", "margin", "Низкая маржа — меньше", "%", "Одна настройка для всего сервиса: «Акции», "
              "«Товары», «Цены» и «Прибыль по товарам» подсвечивают маржу ниже порога. У товара, который продаётся, — "
              "красная.", D(0), D(90), 2),
    Threshold("anomaly_cash_gap_days", "cash", "Кассовый разрыв — смотреть вперёд на", "дн.", "Прогноз остатка ниже нуля "
              "— красная, ниже вашего порога остатка («Планирование») — жёлтая.", D(1), D(90)),
    Threshold("anomaly_uncategorized_days", "cash", "Операция без статьи дольше", "дн.", "Без статьи ДДС по статьям и "
              "прогноз неточны — пора разметить.", D(1), D(90)),
    Threshold("anomaly_payout_diff_pct", "payouts", "Выплата Ozon разошлась с прогнозом больше чем на", "%", "Прогноз — "
              "начисления недели продаж по графику выплат: разница — повод посмотреть удержания.", D(1), D(100)),
    Threshold("anomaly_price_pct", "prices", "Цена изменилась больше чем на", "%", "Жёлтая — посмотрите, кто и зачем "
              "поменял цену.", D(1), D(99)),
    Threshold("anomaly_price_red_pct", "prices", "Цена — красная, больше", "%", "Красная — резкое изменение, похоже на "
              "ошибку.", D(1), D(99)),
    Threshold("anomaly_commission_pp", "prices", "Комиссия изменилась больше чем на", "п. п.", "Вместе с комиссией "
              "меняются маржа и минимальная цена.", D("0.1"), D(50), 1),
    Threshold("anomaly_sync_stale_hours", "data", "Данные не обновлялись дольше", "ч", "Ozon или банк не отвечает: "
              "разовый сбой между плановыми загрузками не тревожит, а долгий — да.", D(1), D(720)),
    Threshold("anomaly_data_gap_days", "data", "Дней без начислений между загруженными — от", "дн.", "Пропуск в "
              "начислениях за 30 дней: выручка, прибыль и P&L за эти дни неполные.", D(1), D(30)),
)
BY_KEY = {t.key: t for t in THRESHOLDS}
assert set(BY_KEY) == set(svc.ANOMALY_FIELDS), "каждый порог аномалий — в своём разделе"

NOTES = {
    "sales": "Последние 3 дня против 7 дней до них — по магазину и по каждому товару, с поправкой на выходные; дни без "
             "данных не считаются, случайные колебания редких продаж — тоже.",
    "stock": "Дни запаса — пороги остатков: меньше «Мало» — жёлтая, меньше «Критично» — красная, больше «Избыток» — "
             "жёлтая. Они же — статусы складов, поэтому меняются в «Настройках → Остатки».",
    "cash": "Только с «Планированием»: прогноз платёжного календаря и операции на счетах.",
    "payouts": "Выплата, которая не пришла по графику, — всегда красная; сверка с банком — с «Планированием» и выпиской.",
    "auto_promo": "Ozon сам добавил товар в акцию — сразу жёлтая, без порогов; маржа в акции ниже нуля — в «Низкой марже».",
    "prices": "Сравниваем с тем, что было 7 дней назад: изменения в кабинете Ozon, самим Ozon и через сервис.",
    "data": "Ключ Ozon или токен банка не принят — красная сразу, без порогов.",
}


class ThresholdError(Exception):
    """Текст — для пользователя."""


def _value(t: Threshold, thresholds: det.Thresholds) -> Decimal:
    return Decimal(str(getattr(thresholds, svc.ANOMALY_FIELDS[t.key])))


def out(s) -> dict:
    now = svc.anomaly_thresholds(s)
    d = det.DEFAULT_THRESHOLDS
    stock = svc.stock_thresholds(s)
    sections = []
    for key in ("sales", "margin", "stock", "cash", "payouts", "auto_promo", "prices", "data"):
        items = [{"key": t.key, "label": t.label, "unit": t.unit, "why": t.why, "value": _value(t, now),
                  "default": _value(t, d), "min": t.min, "max": t.max, "places": t.places}
                 for t in THRESHOLDS if t.section == key]
        sections.append({"section": key, "note": NOTES.get(key), "items": items})
    return {
        "sections": sections,
        "defaults": all(_value(t, now) == _value(t, d) for t in THRESHOLDS),
        "stock": {"critical": stock.critical, "low": stock.low, "excess": stock.excess},
    }


def _checked(t: Threshold, v: Decimal) -> Decimal | int:
    if not t.min <= v <= t.max or v != v.quantize(Decimal(1).scaleb(-t.places)):
        kind = "число" if t.places else "целое число"
        raise ThresholdError(f"«{t.label}» — {kind} от {str(t.min).replace('.', ',')} до {str(t.max).replace('.', ',')}")
    return v if t.places else int(v)


async def update(db: AsyncSession, store_id: uuid.UUID, values: dict[str, Decimal], reset: bool = False):
    """Новые значения порогов магазина (или все — по умолчанию). Ошибка — ThresholdError с понятным текстом."""
    if reset:  # пусто — значение по умолчанию (BUSINESS_LOGIC §7)
        row = await db.get(UserSettings, store_id)
        if row is not None:
            for t in THRESHOLDS:
                setattr(row, t.key, None)
            await db.commit()
        return row or svc.defaults(store_id)
    changes = {}
    for k, v in values.items():
        t = BY_KEY.get(k)
        if t is None:
            raise ThresholdError("Порог: выберите из списка")
        changes[k] = _checked(t, Decimal(v))
    try:
        return await svc.update_settings(db, store_id, changes)
    except svc.SettingsError as exc:
        raise ThresholdError(str(exc)) from exc
