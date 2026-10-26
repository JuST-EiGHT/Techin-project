"""Главная — дашборд Ozon (шаг 3.7): ответ /api/v1/dashboard/ozon."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.ozon_payouts import ForecastOut, WeekBar
from app.schemas.pnl import Coverage

DataSource = Literal["xlsx", "api", "mixed", "none"]


class Setup(BaseModel):
    has_key: bool  # активный ключ Ozon API
    has_reports: bool  # загружен хотя бы один отчёт XLSX
    has_balance: bool  # баланс у Ozon загружался (шаг 3.6)
    tax_set: bool  # налоговый профиль задан


class Figure(BaseModel):
    value: Decimal | None  # null — нет данных, а не 0
    previous: Decimal | None  # за прошлый такой же период
    change_pct: Decimal | None  # изменение, %; null — сравнивать не с чем


class ProfitFigure(Figure):
    kind: Literal["net", "before_tax"]  # остаток после налога или прибыль до налога (налог не задан)


class MarginFigure(BaseModel):
    value: Decimal | None  # маржа, %
    previous: Decimal | None
    change_pp: Decimal | None  # изменение в процентных пунктах


class Figures(BaseModel):
    revenue: Figure  # продано: начисления «Выручка»
    received: Figure  # начислено к получению от Ozon (или своя сумма из отчётов)
    profit: ProfitFigure
    margin: MarginFigure


class SeriesPoint(BaseModel):
    date: date  # день или первый день семидневки
    date_to: date
    prev_date: date | None  # тот же по порядку день прошлого периода
    revenue: Decimal | None  # null — ни одного дня с данными
    prev_revenue: Decimal | None
    partial: bool  # в точке есть дни без данных


class Series(BaseModel):
    granularity: Literal["day", "week"]
    points: list[SeriesPoint]


class DashboardPayouts(BaseModel):
    has_data: bool
    balance: Decimal | None
    last_day: date | None
    next_payout: ForecastOut | None
    upcoming: list[ForecastOut] = []  # выплаты по прогнозу на ближайшие 14 дней — блок «Деньги» на главной (шаг 10.4)
    schedule_title: str
    weeks: list[WeekBar]  # 8 недель назад и 4 вперёд: факт и прогноз


class OzonDashboard(BaseModel):
    date_from: date
    date_to: date
    prev_from: date  # прошлый такой же период
    prev_to: date
    setup: Setup
    data_source: DataSource
    coverage: Coverage
    prev_data_source: DataSource
    missing_cost_count: int  # товаров без себестоимости за период
    figures: Figures
    compare_note: str | None
    series: Series
    payouts: DashboardPayouts
