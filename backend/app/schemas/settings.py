from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Theme = Literal["dark", "light", "system"]
# «Откуда товар»: при закупке, после продажи, с отсрочкой, своё производство, по-разному
SupplierPayment = Literal["upfront", "after_sale", "deferred", "own_production", "mixed"]


class SettingsResponse(BaseModel):
    """Налог и НДС — в налоговом профиле (/api/v1/tax-profile, шаг 2.5)."""

    theme: Theme
    supplier_payment: SupplierPayment
    supplier_deferral_days: int | None = None  # отсрочка оплаты поставщику, дней — у deferred
    cost_label: str  # как называется себестоимость в P&L: «Себестоимость с НДС» / «Долг поставщику» / «… производства»
    price_ad_pct: Decimal  # калькулятор цены: реклама, % от цены
    price_target_margin_pct: Decimal  # целевая маржа, %
    price_defaults: bool  # реклама и маржа ещё не заданы — показаны значения по умолчанию
    # пороги статуса остатка, дней запаса (шаг 5.1): меньше critical — «Критично», меньше low — «Мало»,
    # больше excess — «Избыток»
    stock_critical_days: int
    stock_low_days: int
    stock_excess_days: int
    # планирование поставок (шаг 5.2): запас после поставки, дней, и сколько дней поставка в пути
    supply_target_days: int
    supply_lead_time_days: int
    supply_safety_days: int = 0  # страховой запас сверх цели, дней (10.18)
    # порог «низкой маржи», % (шаг 7.1): акции, страница товаров, прибыль по товарам, аномалия «маржа ниже порога»
    low_margin_pct: Decimal
    # пороги аномалий (шаг 7.1, BUSINESS_LOGIC §7); дни запаса — пороги остатков выше
    anomaly_sales_drop_pct: int  # падение продаж (3 дня против 7 предыдущих): жёлтая
    anomaly_sales_drop_red_pct: int  # красная
    anomaly_sales_growth_pct: int  # рост продаж — проверить цену
    anomaly_price_pct: int  # изменение цены за 7 дней: жёлтая
    anomaly_price_red_pct: int  # красная
    anomaly_commission_pp: Decimal  # изменение комиссии Ozon, п. п.
    anomaly_payout_diff_pct: int  # выплата Ozon разошлась с прогнозом
    anomaly_cash_gap_days: int  # кассовый разрыв: на сколько дней вперёд смотреть (планирование)
    anomaly_uncategorized_days: int  # операция без статьи дольше (планирование)
    anomaly_sync_stale_hours: int  # данные не обновлялись дольше, часов (10.5)
    anomaly_data_gap_days: int  # дней без начислений между загруженными — уже проблема (10.5)
    recommendation_hide_days: int  # «Скрыть» рекомендацию — на сколько дней (10.6)
    abc_a_pct: int  # ABC-анализ (10.10): граница группы A, % накопленной доли
    abc_b_pct: int  # граница группы B
    anomaly_defaults: bool  # пороги аномалий и маржи ещё не меняли


class SettingsUpdate(BaseModel):
    """Не переданное поле не меняется."""

    theme: Theme | None = None
    supplier_payment: SupplierPayment | None = None
    supplier_deferral_days: int | None = Field(None, ge=1, le=365)
    price_ad_pct: Decimal | None = Field(None, ge=0, le=90)
    price_target_margin_pct: Decimal | None = Field(None, ge=0, le=90)
    stock_critical_days: int | None = Field(None, ge=1, le=365)
    stock_low_days: int | None = Field(None, ge=1, le=365)
    stock_excess_days: int | None = Field(None, ge=1, le=365)
    supply_target_days: int | None = Field(None, ge=1, le=365)
    supply_lead_time_days: int | None = Field(None, ge=0, le=180)
    supply_safety_days: int | None = Field(None, ge=0, le=180)
    low_margin_pct: Decimal | None = Field(None, ge=0, le=90, decimal_places=2)
    anomaly_sales_drop_pct: int | None = Field(None, ge=1, le=99)
    anomaly_sales_drop_red_pct: int | None = Field(None, ge=1, le=99)
    anomaly_sales_growth_pct: int | None = Field(None, ge=10, le=1000)
    anomaly_price_pct: int | None = Field(None, ge=1, le=99)
    anomaly_price_red_pct: int | None = Field(None, ge=1, le=99)
    anomaly_commission_pp: Decimal | None = Field(None, ge=Decimal("0.1"), le=50, decimal_places=1)
    anomaly_payout_diff_pct: int | None = Field(None, ge=1, le=100)
    anomaly_cash_gap_days: int | None = Field(None, ge=1, le=90)
    anomaly_uncategorized_days: int | None = Field(None, ge=1, le=90)
    anomaly_sync_stale_hours: int | None = Field(None, ge=1, le=720)
    anomaly_data_gap_days: int | None = Field(None, ge=1, le=30)
    recommendation_hide_days: int | None = Field(None, ge=1, le=90)
    abc_a_pct: int | None = Field(None, ge=10, le=98)
    abc_b_pct: int | None = Field(None, ge=11, le=99)
