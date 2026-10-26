"""Заявки на поставку FBO (шаг 10.18): разбор ответов Ozon, статусы простыми словами, план против факта. Чистые функции —
без БД и HTTP.

- Заявка — `/v3/supply-order/get`: номер, даты, статус, таймслот, пункт отгрузки, поставки (склад хранения, кластер,
  `bundle_id` — состав). Состав — `/v1/supply-order/bundle`, что приняли — акты `/v1/supply-order/act/product/get` (бета):
  заявлено, принято фактически, согласовано.
- «План против факта» по товару: в плане (если заявка создана из плана сервиса) → заявлено в заявке → принято. Разница
  «принято − заявлено» — недовоз (меньше нуля) или излишек; актов ещё нет — «нет данных», а не 0 (правило 12).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# статусы /v3/supply-order/get (выгрузка документации 2026-10)
DATA_FILLING, READY_TO_SUPPLY = "DATA_FILLING", "READY_TO_SUPPLY"
ACCEPTED_AT_SUPPLY_WAREHOUSE, IN_TRANSIT = "ACCEPTED_AT_SUPPLY_WAREHOUSE", "IN_TRANSIT"
ACCEPTANCE_AT_STORAGE_WAREHOUSE = "ACCEPTANCE_AT_STORAGE_WAREHOUSE"
REPORTS_CONFIRMATION_AWAITING, REPORT_REJECTED = "REPORTS_CONFIRMATION_AWAITING", "REPORT_REJECTED"
COMPLETED, REJECTED_AT_SUPPLY_WAREHOUSE = "COMPLETED", "REJECTED_AT_SUPPLY_WAREHOUSE"
CANCELLED, OVERDUE, UNSPECIFIED = "CANCELLED", "OVERDUE", "UNSPECIFIED"
STATES = (
    DATA_FILLING, READY_TO_SUPPLY, ACCEPTED_AT_SUPPLY_WAREHOUSE, IN_TRANSIT, ACCEPTANCE_AT_STORAGE_WAREHOUSE,
    REPORTS_CONFIRMATION_AWAITING, REPORT_REJECTED, COMPLETED, REJECTED_AT_SUPPLY_WAREHOUSE, CANCELLED, OVERDUE,
)
STATE_TEXT = {
    DATA_FILLING: "Заполнение данных", READY_TO_SUPPLY: "Готова к отгрузке",
    ACCEPTED_AT_SUPPLY_WAREHOUSE: "Принята на точке отгрузки", IN_TRANSIT: "В пути",
    ACCEPTANCE_AT_STORAGE_WAREHOUSE: "Приёмка на складе", REPORTS_CONFIRMATION_AWAITING: "Согласование актов",
    REPORT_REJECTED: "Спор по акту", COMPLETED: "Завершена", REJECTED_AT_SUPPLY_WAREHOUSE: "Отказано в приёмке",
    CANCELLED: "Отменена", OVERDUE: "Просрочена", UNSPECIFIED: "Статус не указан",
}
# группы для фильтра и календаря
PREPARING, ON_THE_WAY, ACCEPTING, DONE, FAILED = "preparing", "on_the_way", "accepting", "done", "failed"
GROUP = {
    DATA_FILLING: PREPARING, READY_TO_SUPPLY: PREPARING, UNSPECIFIED: PREPARING,
    ACCEPTED_AT_SUPPLY_WAREHOUSE: ON_THE_WAY, IN_TRANSIT: ON_THE_WAY,
    ACCEPTANCE_AT_STORAGE_WAREHOUSE: ACCEPTING, REPORTS_CONFIRMATION_AWAITING: ACCEPTING, REPORT_REJECTED: ACCEPTING,
    COMPLETED: DONE, REJECTED_AT_SUPPLY_WAREHOUSE: FAILED, CANCELLED: FAILED, OVERDUE: FAILED,
}
GROUPS = (PREPARING, ON_THE_WAY, ACCEPTING, DONE, FAILED)
GROUP_TEXT = {PREPARING: "Готовятся", ON_THE_WAY: "В пути", ACCEPTING: "Приёмка", DONE: "Завершены",
              FAILED: "Отменены и отказы"}
# статус больше не меняется: состав и акты не перечитываем (кроме случая, когда актов ещё нет)
FINAL = frozenset({COMPLETED, REJECTED_AT_SUPPLY_WAREHOUSE, CANCELLED, OVERDUE})
# акты появляются с приёмки на складе
WITH_ACTS = frozenset({ACCEPTANCE_AT_STORAGE_WAREHOUSE, REPORTS_CONFIRMATION_AWAITING, REPORT_REJECTED, COMPLETED})


def group_of(state: str) -> str:
    return GROUP.get(state, PREPARING)


def state_text(state: str) -> str:
    return STATE_TEXT.get(state, state.replace("_", " ").capitalize())


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v or None


def _str(value: Any, limit: int = 255) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] or None


@dataclass(frozen=True)
class Supply:
    supply_id: int | None
    bundle_id: str | None
    storage_warehouse_id: int | None
    storage_warehouse: str | None
    cluster_id: int | None  # macrolocal_cluster_id
    state: str | None


@dataclass
class Order:
    order_id: int
    number: str | None
    created_at: datetime | None
    state: str
    state_updated_at: datetime | None
    timeslot_from: datetime | None
    timeslot_to: datetime | None
    timezone: str | None
    dropoff: str | None
    supplies: list[Supply] = field(default_factory=list)

    @property
    def storage_warehouse(self) -> str | None:
        names = [s.storage_warehouse for s in self.supplies if s.storage_warehouse]
        return ", ".join(dict.fromkeys(names)) or None


def parse_order(raw: dict) -> Order | None:
    """Заявка из `/v3/supply-order/get`; без идентификатора — None. Ozon может добавить поля — читаем только нужные."""
    order_id = _int(raw.get("order_id"))
    if order_id is None:
        return None
    slot = ((raw.get("timeslot") or {}).get("timeslot")) or {}
    tz = ((raw.get("timeslot") or {}).get("timezone_info")) or {}
    supplies = []
    for s in raw.get("supplies") or []:
        if not isinstance(s, dict):
            continue
        wh = s.get("storage_warehouse") or {}
        bundle = s.get("bundle_id") or ((s.get("content") or {}).get("bundle_id"))
        supplies.append(Supply(
            supply_id=_int(s.get("supply_id")), bundle_id=_str(bundle, 100),
            storage_warehouse_id=_int(wh.get("warehouse_id")), storage_warehouse=_str(wh.get("name")),
            cluster_id=_int(s.get("macrolocal_cluster_id")), state=_str(s.get("state") or s.get("supply_state"), 40),
        ))
    state = _str(raw.get("state"), 40) or UNSPECIFIED
    return Order(
        order_id=order_id, number=_str(raw.get("order_number"), 64), created_at=_dt(raw.get("created_date")),
        state=state, state_updated_at=_dt(raw.get("state_updated_date")), timeslot_from=_dt(slot.get("from")),
        timeslot_to=_dt(slot.get("to")), timezone=_str(tz.get("iana_name"), 64),
        dropoff=_str((raw.get("dropoff_warehouse") or {}).get("name")), supplies=supplies,
    )


@dataclass(frozen=True)
class Line:
    """Строка состава или акта: SKU, артикул, сколько."""

    sku: int | None
    offer_id: str | None
    name: str | None
    quantity: int  # в составе — заявлено; в акте — заявлено по акту
    fact: int | None = None  # в акте: принято фактически
    approved: int | None = None  # в акте: согласовано
    defect: bool = False


def parse_bundle(items: list[dict]) -> list[Line]:
    """Состав `/v1/supply-order/bundle`: одинаковые SKU (разные кванты) — одной строкой."""
    out: dict[tuple, Line] = {}
    for i in items:
        if not isinstance(i, dict):
            continue
        key = (_int(i.get("sku")), _str(i.get("offer_id"), 100))
        qty = max(_int(i.get("quantity")) or 0, 0)
        prev = out.get(key)
        out[key] = Line(key[0], key[1], _str(i.get("name")), qty + (prev.quantity if prev else 0))
    return list(out.values())


def parse_act(resp: dict) -> list[Line]:
    """Товары в актах поставки `/v1/supply-order/act/product/get`: по SKU сумма по актам; брак — пометкой."""
    defects = {_int(d.get("sku")) for d in resp.get("skus_defects") or [] if isinstance(d, dict)}
    out: dict[tuple, list] = {}
    for act in resp.get("supply_acts") or []:
        for i in (act or {}).get("items") or []:
            info = i.get("sku_info") or {}
            key = (_int(info.get("sku")), _str(info.get("offer_id"), 100))
            acc = out.setdefault(key, [_str(info.get("name")), 0, 0, 0])
            acc[1] += max(_int(i.get("declared_quantity")) or 0, 0)
            acc[2] += max(_int(i.get("fact_quantity")) or 0, 0)
            acc[3] += max(_int(i.get("approved_quantity")) or 0, 0)
    return [Line(sku, offer, name, declared, fact, approved, sku in defects)
            for (sku, offer), (name, declared, fact, approved) in out.items()]


def content_key(lines: list[Line]) -> tuple:
    """Состав для сравнения со снимком: изменился — новая запись."""
    return tuple(sorted((line.sku or 0, line.offer_id or "", line.quantity, line.fact, line.approved, line.defect)
                        for line in lines))


@dataclass
class Result:
    """Товар в поставке: план → заявлено → принято."""

    key: Any  # id товара или (sku, артикул) у товара не из каталога
    planned: int | None  # в плане сервиса; None — заявка не из плана
    declared: int | None  # в заявке Ozon; None — состав не загружен
    accepted: int | None  # принято по актам; None — актов ещё нет

    @property
    def diff(self) -> int | None:
        """Принято − заявлено: меньше нуля — недовоз, больше — излишек."""
        if self.accepted is None or self.declared is None:
            return None
        return self.accepted - self.declared

    @property
    def plan_diff(self) -> int | None:
        """Заявлено − в плане: изменили количество при оформлении."""
        if self.planned is None or self.declared is None:
            return None
        return self.declared - self.planned


def results(planned: dict, declared: dict | None, accepted: dict | None) -> list[Result]:
    """По каждому товару из плана, заявки и актов. declared / accepted None — данных ещё нет (а не 0)."""
    keys = list(dict.fromkeys([*planned, *(declared or {}), *(accepted or {})]))
    return [Result(
        k, planned.get(k) if planned else None,
        None if declared is None else declared.get(k, 0),
        None if accepted is None else accepted.get(k, 0),
    ) for k in keys]


def totals(rows: list[Result]) -> dict:
    """Итого по поставке; часть данных неизвестна — итога по ней нет."""
    def total(attr: str) -> int | None:
        values = [getattr(r, attr) for r in rows]
        return None if not values or any(v is None for v in values) else sum(values)

    planned = [r.planned for r in rows if r.planned is not None]
    return {
        "planned": sum(planned) if planned else None, "declared": total("declared"), "accepted": total("accepted"),
        "diff": total("diff"), "short": sum(-r.diff for r in rows if r.diff is not None and r.diff < 0),
    }
