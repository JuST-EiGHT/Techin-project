"""Налоговый профиль (шаг 2.5): кто продавец, система налогообложения, НДС — с историей,
справочник для мастера и предпросмотр «как посчитаем налог»."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine import tax as engine
from app.models.product_cost import BEGINNING
from app.models.report import Report
from app.models.tax_profile import TaxProfile
from app.models.user import User
from app.schemas.plan_calendar import PastChange, UpcomingTaxes
from app.schemas.report import NormalizedReportData
from app.schemas.tax import (
    RegimeOption,
    TaxExample,
    TaxOptions,
    TaxPreview,
    TaxProfileIn,
    TaxProfileOut,
    TaxProfileState,
)
from app.services import plan_calendar
from app.services import tax_profile as svc
from app.services.auth import get_current_user
from app.services.pnl_stale import affected_period, invalidate
from app.services.sync.finance import today_msk
from app.services.templates.values import VAT_RATES

router = APIRouter(prefix="/tax-profile", tags=["tax"])

# пример для мастера: продажа покупателю на 10 000 ₽, Ozon удержал 4 000 ₽, закупка 3 000 ₽ без НДС, месяц
EXAMPLE = {"income": Decimal(10000), "ozon": Decimal(4000), "cost": Decimal(3000), "days": 30}


def _q(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(Decimal("0.01"))


def _out(p: TaxProfile) -> TaxProfileOut:
    return TaxProfileOut(
        id=p.id,
        effective_from=p.effective_from,
        legal_form=p.legal_form,
        regime=p.regime,
        rate_pct=_q(p.rate_pct),
        custom_base=p.custom_base,
        vat_mode=p.vat_mode,
        vat_rate_pct=_q(p.vat_rate_pct),
        supplier_vat_pct=_q(p.supplier_vat_pct),
        contributions_year=_q(p.contributions_year),
        has_employees=p.has_employees,
        patent_cost_year=_q(p.patent_cost_year),
        created_at=p.created_at,
        title=svc.profile_title(p),
    )


async def _state(db: AsyncSession, user: User, created: bool | None = None) -> TaxProfileState:
    profiles = await svc.load_profiles(db, user.id)
    current = svc.profile_on(profiles, today_msk())
    return TaxProfileState(
        current=_out(current) if current else None, history=[_out(p) for p in reversed(profiles)], created=created
    )


def _data(body: TaxProfileIn) -> svc.ProfileData:
    return svc.ProfileData(**body.model_dump(exclude={"effective_from", "past_payments"}))


@router.get("", response_model=TaxProfileState)
async def get_tax_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Действующий профиль и история. current=null — налог «не задан»."""
    return await _state(db, user)


@router.put("", response_model=TaxProfileState)
async def save_tax_profile(
    body: TaxProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Новый профиль — новая запись в истории; прошлые периоды считаются по прежнему профилю. Будущие налоговые
    платежи календаря пересчитываются сами; прошлые (профиль задним числом) — как ответил пользователь
    (past_payments): keep — оставить как были."""
    dates = [p.effective_from for p in await svc.load_profiles(db, user.id)]
    try:
        record, created = await svc.save_profile(db, user.id, _data(body), body.effective_from)
    except svc.TaxProfileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if created:
        start, end = affected_period(dates, record.effective_from)
        await invalidate(db, user.id, start, end, "Изменён налоговый профиль")
        if body.past_payments == "keep" and record.effective_from < today_msk():
            db.add(plan_calendar.freeze_past(user.id, record, today_msk()))
    await db.commit()
    return await _state(db, user, created)


@router.get("/upcoming", response_model=UpcomingTaxes)
async def upcoming(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """«Ближайшие сроки»: 2–3 следующих налоговых платежа — что, до какой даты, примерная сумма и расчёт.
    Профиль не задан — has_profile=false, платежей нет."""
    return await plan_calendar.upcoming_taxes(db, user.id)


@router.post("/past-payments", response_model=list[PastChange])
async def past_payments(body: TaxProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Какие прошлые неоплаченные налоговые платежи изменятся, если сохранить такой профиль (с даты effective_from).
    Пусто — спрашивать «пересчитать прошлые платежи?» незачем. Ничего не сохраняет."""
    profiles = await svc.load_profiles(db, user.id)
    effective_from = body.effective_from or (today_msk() if profiles else BEGINNING)
    try:
        data = svc.validate(_data(body), effective_from)
    except svc.TaxProfileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    candidate = TaxProfile(
        id=uuid.uuid4(), user_id=user.id, effective_from=effective_from, created_at=datetime.now(timezone.utc),
        **{name: getattr(data, name) for name in svc.FIELDS},
    )
    return await plan_calendar.past_changes(db, user.id, candidate)


@router.get("/options", response_model=TaxOptions)
async def tax_options(
    on: date | None = Query(None, description="На какую дату ставки; по умолчанию — сегодня"),
    user: User = Depends(get_current_user),
):
    day = on or today_msk()
    vat = engine.vat_rules(day)
    return TaxOptions(
        forms=engine.FORMS,
        regimes=[
            RegimeOption(
                key=r.key,
                title=r.title,
                summary=r.summary,
                forms=list(r.forms),
                vat_modes=list(r.vat_modes),
                default_rates={form: engine.default_rate(r.key, form, day) for form in r.forms},
                min_rate=engine.min_rate(r.key, day),
                restriction=r.restriction,
                how=engine.how(engine.Profile(r.key, r.forms[0]), day),
            )
            for r in engine.REGIMES.values()
        ],
        vat_modes=engine.VAT_MODES,
        vat_rates={mode: engine.vat_rates(mode, day) for mode in engine.VAT_MODES},
        supplier_vat_rates=[r for r in VAT_RATES if r != 18],
        supplier_default=vat["supplier_default"],
        limits={k: v for k, v in engine.limits(day).items() if k != "from"},
    )


def _example(heading: str, data: engine.TaxInput, result: engine.TaxResult) -> TaxExample:
    return TaxExample(
        title=heading,
        income=_q(data.income),
        ozon_expenses=_q(data.ozon_expenses),
        cost_sales=_q(data.cost_sales),
        cost_with_vat=_q(data.cost_with_vat),
        tax=result.tax,
        vat=result.vat,
        total=result.total,
        lines=result.lines,
        warnings=result.warnings,
    )


async def _report_example(db: AsyncSession, user: User, data: svc.ProfileData) -> TaxExample | None:
    """Тот же расчёт на последнем загруженном отчёте — чтобы увидеть налог на своих цифрах."""
    res = await db.execute(
        select(Report)
        .where(Report.user_id == user.id, Report.deleted_at.is_(None))
        .order_by(Report.period_end.desc().nulls_last(), Report.uploaded_at.desc())
        .limit(1)
    )
    report = res.scalar_one_or_none()
    if report is None:
        return None
    summary = NormalizedReportData.model_validate(report.summary_json)
    day = summary.period_start or today_msk()
    supplier = 1 + data.supplier_vat_pct / 100
    payout = report.manual_total if report.manual_total is not None else None
    tax_input = engine.report_tax_input(summary, (summary.cost_sales * supplier).quantize(Decimal("0.01")), payout)
    kind = "Отчёт по начислениям" if summary.report_type == "accrual" else "Отчёт о реализации"
    profile = _engine_profile(data)
    result = engine.compute_tax(profile, tax_input, day)
    if summary.missing_cost and engine.uses_costs(profile):  # на «доходах» себестоимость налог не меняет
        result.warnings.append(f"Себестоимость не найдена для {len(summary.missing_cost)} арт. — расходы занижены.")
    return _example(f"{kind} {summary.period}", tax_input, result)


def _engine_profile(data: svc.ProfileData) -> engine.Profile:
    return engine.Profile(
        regime=data.regime,
        legal_form=data.legal_form,
        rate_pct=data.rate_pct,
        custom_base=data.custom_base or "profit",
        vat_mode=data.vat_mode,
        vat_rate_pct=data.vat_rate_pct,
        supplier_vat_pct=data.supplier_vat_pct,
        contributions_year=data.contributions_year,
        has_employees=data.has_employees,
        patent_cost_year=data.patent_cost_year,
    )


@router.post("/preview", response_model=TaxPreview)
async def preview_tax(body: TaxProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Как посчитается налог при таком профиле: словами и на примерах. Ничего не сохраняет."""
    day = body.effective_from or today_msk()
    try:
        data = svc.validate(_data(body), day)
    except svc.TaxProfileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    profile = _engine_profile(data)
    cost_with_vat = (EXAMPLE["cost"] * (1 + data.supplier_vat_pct / 100)).quantize(Decimal("0.01"))
    sample = engine.TaxInput(
        income=EXAMPLE["income"],
        ozon_expenses=EXAMPLE["ozon"],
        cost_sales=EXAMPLE["cost"],
        cost_with_vat=cost_with_vat,
        payout=EXAMPLE["income"] - EXAMPLE["ozon"],
        revenue_base=EXAMPLE["income"],
        days=EXAMPLE["days"],
        income_note="цена продажи покупателю",
    )
    heading = "Продажа на 10 000 ₽: Ozon удержал 4 000 ₽, закупка 3 000 ₽ без НДС, за месяц"
    return TaxPreview(
        title=svc.profile_title(data),
        how=engine.how(profile, day),
        example=_example(heading, sample, engine.compute_tax(profile, sample, day)),
        report_example=await _report_example(db, user, data),
    )
