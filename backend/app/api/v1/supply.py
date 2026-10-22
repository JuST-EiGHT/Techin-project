"""Планирование поставок (шаг 5.2): что, куда и сколько везти; Excel для логиста."""
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files import attachment
from app.db.session import get_db
from app.engine.supply_planner import Params
from app.models.user import User
from app.schemas.supply import SupplyReport
from app.services import supply as svc
from app.services.stores import get_current_store
from app.services.sync.finance import today_msk

router = APIRouter(prefix="/supply", tags=["supply"])


class _ParamsQuery:
    """Параметры из запроса; не переданные — сохранённые у пользователя (Основные настройки → Поставки)."""

    def __init__(
        self,
        target_days: int | None = Query(None, ge=1, le=365, description="Запас после поставки, дней продаж"),
        lead_time_days: int | None = Query(None, ge=0, le=180, description="Сколько дней поставка в пути"),
    ):
        self.target_days, self.lead_time_days = target_days, lead_time_days

    async def params(self, db: AsyncSession, user: User) -> Params:
        base = await svc.load_params(db, user.id)
        return svc.with_overrides(base, target_days=self.target_days, lead_time_days=self.lead_time_days)


@router.get("/recommendations", response_model=SupplyReport)
async def recommendations(
    q: _ParamsQuery = Depends(), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)
):
    """Рекомендации по паре товар × кластер Ozon (поставка) и товар × свой склад (закупка): продажи за 30 и 7 дней,
    на сколько дней хватит, сколько везти, срочность. Только товары с заказами за 30 дней (BUSINESS_LOGIC §5)."""
    return await svc.report(db, user.id, await q.params(db, user))


@router.get("/export", response_class=Response)
async def export(q: _ParamsQuery = Depends(), user: User = Depends(get_current_store), db: AsyncSession = Depends(get_db)):
    """Те же рекомендации в Excel для логиста: по кластеру и складу, срочные первыми; на втором листе — параметры."""
    data = await svc.report(db, user.id, await q.params(db, user))
    return attachment(
        svc.to_xlsx(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"Поставки {today_msk():%d.%m.%Y}.xlsx", "supply.xlsx",
    )
