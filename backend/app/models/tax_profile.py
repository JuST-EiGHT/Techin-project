import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.engine.tax import Profile


class TaxProfile(Base):
    """Налоговый профиль продавца (шаг 2.5): кто он, какая система налогообложения, НДС.

    История (правило 12): смена режима или ставки — новая запись с effective_from, прежние не
    меняются, прошлые периоды считаются по своему профилю. Нет записей — налог «не задан».
    """

    __tablename__ = "tax_profiles"
    __table_args__ = (Index("ix_tax_profiles_user_from", "user_id", "effective_from"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    legal_form: Mapped[str | None] = mapped_column(String(16))  # ip | ooo | self_employed
    regime: Mapped[str] = mapped_column(String(24), nullable=False)  # engine.tax.REGIMES
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # ставка налога, %; у патента — нет
    custom_base: Mapped[str | None] = mapped_column(String(8))  # «свой процент»: profit | revenue
    vat_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="none")  # none | special | general
    vat_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal(0))
    supplier_vat_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal(22))
    contributions_year: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # страховые взносы за год, ₽
    has_employees: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    patent_cost_year: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # ПСН: стоимость патента за год
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_engine(self) -> Profile:
        return Profile(
            regime=self.regime,
            legal_form=self.legal_form,
            rate_pct=None if self.rate_pct is None else Decimal(self.rate_pct),
            custom_base=self.custom_base or "profit",
            vat_mode=self.vat_mode,
            vat_rate_pct=Decimal(self.vat_rate_pct),
            supplier_vat_pct=Decimal(self.supplier_vat_pct),
            contributions_year=None if self.contributions_year is None else Decimal(self.contributions_year),
            has_employees=self.has_employees,
            patent_cost_year=None if self.patent_cost_year is None else Decimal(self.patent_cost_year),
        )
