"""Налоговый профиль пользователя (шаг 2.5): история, профиль на дату, проверка, сохранение.

Профиль на дату D — последний с effective_from ≤ D; для дат раньше первого — первый (как у
себестоимости). Нет профиля — налог «не задан»: ни 0, ни чужие 20 % (правила 12, 13).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.tax import FORMS, REGIMES, default_rate, vat_rates, vat_rules
from app.models.product_cost import BEGINNING
from app.models.tax_profile import TaxProfile
from app.services.sync.finance import today_msk
from app.services.templates.values import VAT_RATES

FIELDS = (
    "legal_form",
    "regime",
    "rate_pct",
    "custom_base",
    "vat_mode",
    "vat_rate_pct",
    "supplier_vat_pct",
    "contributions_year",
    "has_employees",
    "patent_cost_year",
)


class TaxProfileError(Exception):
    """Профиль не подходит. Текст — для пользователя."""


async def load_profiles(db: AsyncSession, user_id: uuid.UUID) -> list[TaxProfile]:
    """Все записи пользователя: по дате начала действия, при равных — по времени записи."""
    res = await db.execute(select(TaxProfile).where(TaxProfile.user_id == user_id))
    return sorted(res.scalars(), key=lambda p: (p.effective_from, _created(p)))


def _created(p: TaxProfile) -> datetime:
    created = p.created_at or datetime.min
    return created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created


def profile_on(profiles: list[TaxProfile], day: date) -> TaxProfile | None:
    current = None
    for p in profiles:
        if p.effective_from <= day:
            current = p
    return current or (profiles[0] if profiles else None)


async def get_profile(db: AsyncSession, user_id: uuid.UUID, day: date | None = None) -> TaxProfile | None:
    return profile_on(await load_profiles(db, user_id), day or today_msk())


def default_supplier_vat_pct(day: date) -> Decimal:
    return Decimal(vat_rules(day)["supplier_default"])


async def supplier_vat_multiplier(db: AsyncSession, user_id: uuid.UUID, day: date | None = None) -> Decimal:
    """НДС в цене поставщика по умолчанию — коэффициентом: из профиля, без профиля — общая ставка."""
    day = day or today_msk()
    profile = await get_profile(db, user_id, day)
    pct = Decimal(profile.supplier_vat_pct) if profile else default_supplier_vat_pct(day)
    return (1 + pct / 100).quantize(Decimal("0.001"))


@dataclass
class ProfileData:
    regime: str
    legal_form: str | None = None
    rate_pct: Decimal | None = None
    custom_base: str | None = None
    vat_mode: str = "none"
    vat_rate_pct: Decimal = Decimal(0)
    supplier_vat_pct: Decimal = Decimal(22)
    contributions_year: Decimal | None = None
    has_employees: bool = False
    patent_cost_year: Decimal | None = None


def validate(data: ProfileData, day: date) -> ProfileData:
    """Проверяет сочетание и подставляет стандартную ставку. Ошибка — TaxProfileError с текстом."""
    regime = REGIMES.get(data.regime)
    if regime is None:
        raise TaxProfileError("Выберите систему налогообложения.")
    if regime.key != "custom":
        if data.legal_form not in FORMS:
            raise TaxProfileError("Укажите, кто вы: ИП, ООО или самозанятый.")
        if data.legal_form not in regime.forms:
            raise TaxProfileError(f"Режим «{regime.title}» недоступен для формы «{FORMS[data.legal_form]}».")
    elif data.legal_form is not None and data.legal_form not in FORMS:
        raise TaxProfileError("Укажите, кто вы: ИП, ООО или самозанятый.")
    if data.vat_mode not in regime.vat_modes:
        raise TaxProfileError(f"При режиме «{regime.title}» такой вариант НДС невозможен.")
    allowed = vat_rates(data.vat_mode, day)
    if data.vat_mode == "none":
        data.vat_rate_pct = Decimal(0)
    elif data.vat_rate_pct not in allowed:
        rates = ", ".join(f"{r} %" for r in allowed)
        raise TaxProfileError(f"Ставка НДС для этого варианта — {rates}.")
    if data.supplier_vat_pct not in VAT_RATES:
        raise TaxProfileError("НДС в цене поставщика — 22 %, 20 %, 10 %, 7 %, 5 % или 0 % (без НДС).")

    if regime.kind == "custom":
        if data.rate_pct is None or data.rate_pct <= 0:
            raise TaxProfileError("Укажите свой процент налога — больше 0.")
        if data.custom_base not in ("profit", "revenue"):
            raise TaxProfileError("Укажите, от чего считать свой процент: от прибыли или от дохода.")
    else:
        data.custom_base = None
    if regime.kind == "patent":
        if data.patent_cost_year is None or data.patent_cost_year <= 0:
            raise TaxProfileError("Укажите стоимость патента за год.")
        data.rate_pct = None
    else:
        data.patent_cost_year = None
        if data.rate_pct is None:
            data.rate_pct = default_rate(data.regime, data.legal_form, day)
    if data.legal_form == "self_employed" or regime.key in ("npd",):
        data.has_employees = False
    return data


def _same(record: TaxProfile, data: ProfileData) -> bool:
    for name in FIELDS:
        a, b = getattr(record, name), getattr(data, name)
        if isinstance(a, Decimal) or isinstance(b, Decimal):
            if (a is None) != (b is None) or (a is not None and Decimal(a) != Decimal(b)):
                return False
        elif a != b:
            return False
    return True


async def save_profile(
    db: AsyncSession, user_id: uuid.UUID, data: ProfileData, effective_from: date | None = None
) -> tuple[TaxProfile, bool]:
    """Новый профиль — новая запись (прежние не меняются). Без даты: первый — с начала учёта
    (действует и для уже загруженных отчётов), следующие — с сегодня. Такой же, как действующий
    на эту дату, — без новой записи. Возвращает (запись, создана ли новая). Коммит — на вызывающем."""
    profiles = await load_profiles(db, user_id)
    if effective_from is None:
        effective_from = today_msk() if profiles else BEGINNING
    data = validate(data, effective_from)
    current = profile_on(profiles, effective_from)
    if current is not None and current.effective_from <= effective_from and _same(current, data):
        return current, False
    record = TaxProfile(
        id=uuid.uuid4(),
        user_id=user_id,
        effective_from=effective_from,
        created_at=datetime.now(timezone.utc),
        **{name: getattr(data, name) for name in FIELDS},
    )
    db.add(record)
    return record, True
