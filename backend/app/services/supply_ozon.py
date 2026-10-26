"""Заявка на поставку FBO в Ozon по плану (шаг 10.18; правило 6 — предпросмотр → подтверждение → журнал).

1. «Проверить в Ozon» — черновик прямой поставки (`/v1/draft/direct/create`: SKU и количество, кластер — его
   `macrolocal_cluster_id` из `/v1/cluster/list` по названию). Черновик в кабинете не виден, ни к чему не обязывает и живёт
   у Ozon 30 минут. Ответ (`/v2/draft/create/info`): склады кластера с доступностью, товары, которые Ozon не примет, ошибки.
2. Таймслоты выбранного склада — `/v2/draft/timeslot/info` (не дальше 28 дней).
3. «Создать заявку» — только по готовому черновику, один раз, если план не меняли после проверки, с выбранными складом и
   таймслотом и подтверждением: `/v2/draft/supply/create` → `/v2/draft/supply/create/status` → номер заявки. Запрос и ответ
   Ozon — в журнале действий (`audit_log`). Дальше заявку ведут в кабинете Ozon (грузоместа, пропуск), статусы — синхронизация.
Лимиты Ozon: черновики и заявки — 2 в минуту, 50 в час, 500 в день (429 — «подождите минуту»).
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import supply_planner as planner
from app.integrations.ozon.errors import (
    OzonAPIError,
    OzonAuthError,
    OzonNotConfiguredError,
    OzonRateLimitError,
    OzonTimeoutError,
)
from app.integrations.ozon.factory import get_ozon_client
from app.models.audit import APPLIED, FAILED, REJECTED, UNKNOWN
from app.models.product import Product
from app.models.supply import (
    DRAFT_FAILED,
    DRAFT_PENDING,
    DRAFT_READY,
    DRAFT_USED,
    PLAN_DRAFT,
    SupplyDraft,
    SupplyPlan,
)
from app.schemas.sync import utc
from app.services import audit
from app.services.supply import cluster_key
from app.services.supply_plans import PlanError, current_items, items_key, mark_ordered
from app.services.sync.finance import today_msk
from app.services.sync.supply_orders import fetch_clusters

DRAFT_CREATE = "/v1/draft/direct/create"
DRAFT_INFO = "/v2/draft/create/info"
TIMESLOTS = "/v2/draft/timeslot/info"
SUPPLY_CREATE = "/v2/draft/supply/create"
SUPPLY_STATUS = "/v2/draft/supply/create/status"
SUPPLY_TYPE = "DIRECT"
DRAFT_TTL = timedelta(minutes=25)  # Ozon хранит черновик 30 минут — берём с запасом
TIMESLOT_MAX_DAYS = 28
POLL_ATTEMPTS = 5
POLL_DELAY = 1.0  # секунд между опросами статуса
ENTITY = "supply_order"

ERROR_TEXT = {
    "ORDER_SKU_LIMIT": "в поставке больше 5 000 товаров",
    "INVALID_QUANTITY_OR_QUANT": "неверное количество товара или кванта",
    "ORDER_ALREADY_CREATED": "заявка по этому черновику уже создана",
    "ORDER_CREATION_IN_PROGRESS": "заявка уже создаётся",
    "DRAFT_DOES_NOT_EXIST": "черновик истёк — проверьте план в Ozon заново",
    "CONTRACTOR_CAN_NOT_CREATE_ORDER": "Ozon не разрешает этому кабинету создавать заявки",
    "INACTIVE_CONTRACT": "договор с Ozon неактивен",
    "DRAFT_INCORRECT_STATE": "черновик в неподходящем состоянии — проверьте план заново",
    "INVALID_VOLUME": "неверный объём поставки",
    "INVALID_ROUTE": "неверный маршрут",
    "INVALID_STORAGE_WAREHOUSE": "склад недоступен для поставки",
    "TIMESLOT_NOT_AVAILABLE": "таймслот уже занят — выберите другой",
    "INVALID_SUPPLY_CONTENT": "Ozon не принял состав поставки",
    "DRAFT_IS_LOCKED": "черновик заблокирован — проверьте план заново",
    "MINIMUM_VOLUME_IN_LITRES_INVALID": "поставка меньше минимального объёма",
    "CAN_NOT_CREATE_ORDER": "Ozon не смог создать заявку",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ozon_error(exc: OzonAPIError) -> PlanError:
    if isinstance(exc, OzonNotConfiguredError | OzonAuthError):
        return PlanError(f"{exc.message}. Для заявок на поставку ключу нужны права на поставки FBO.", 400)
    if isinstance(exc, OzonRateLimitError):
        return PlanError("Ozon разрешает 2 черновика или заявки в минуту — подождите минуту и повторите", 429)
    return PlanError(f"Ozon не ответил как нужно: {exc.message}", 502)


def _reasons(codes: list | None) -> list[str]:
    return [ERROR_TEXT.get(c, c) for c in codes or [] if c and c != "UNSPECIFIED"]


async def _plan_for_ozon(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> tuple[SupplyPlan, list]:
    plan = await db.get(SupplyPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise PlanError("План поставки не найден", 404)
    if plan.kind != planner.SUPPLY:
        raise PlanError("Это закупка на свой склад — заявка в Ozon для неё не нужна")
    if plan.status != PLAN_DRAFT:
        raise PlanError("По плану уже есть заявка или он отменён")
    items = [i for i in (await current_items(db, [plan.id]))[plan.id] if i.qty > 0]
    if not items:
        raise PlanError("В плане нет ни одной штуки")
    return plan, items


def draft_out(d: SupplyDraft, skus: dict[int, dict] | None = None) -> dict:
    """Что ответил Ozon по черновику: склады кластера, отказы по товарам, ошибки."""
    resp = d.response_json or {}
    skus = skus or {}
    warehouses, cluster_name = [], None
    for c in resp.get("clusters") or []:
        cluster_name = cluster_name or c.get("cluster_name")
        for w in c.get("warehouses") or []:
            sw = w.get("storage_warehouse") or {}
            status = w.get("availability_status") or {}
            if not sw.get("warehouse_id"):
                continue
            # значения state в документации не перечислены: склад недоступен, если Ozon назвал причину или «не доступен»
            reason = None if status.get("invalid_reason") in (None, "", "UNSPECIFIED") else status.get("invalid_reason")
            state = str(status.get("state") or "")
            warehouses.append({
                "id": int(sw["warehouse_id"]), "name": sw.get("name") or str(sw["warehouse_id"]),
                "address": sw.get("address"), "available": reason is None and "NOT" not in state and "UNAVAILABLE" not in state,
                "state": state or None, "reason": reason, "rank": w.get("total_rank"),
            })
    rejected, errors = [], []
    for e in resp.get("errors") or []:
        msg = e.get("message") or (e.get("error_message") if e.get("error_message") not in (None, "UNSPECIFIED") else None)
        if msg:
            errors.append(msg)
        errors.extend(_reasons(e.get("error_reasons")))
        for v in e.get("items_validation") or []:
            for r in v.get("rejected_items") or []:
                sku = int(r.get("sku") or 0)
                p = skus.get(sku, {})
                rejected.append({"sku": sku, "offer_id": p.get("offer_id"), "product_id": p.get("id"),
                                 "reasons": [x for x in r.get("reasons") or [] if x != "UNSPECIFIED"]})
    warehouses.sort(key=lambda w: (not w["available"], w["rank"] if w["rank"] is not None else 10 ** 6, w["name"]))
    return {
        "id": d.id, "plan_id": d.plan_id, "status": d.status, "cluster_name": cluster_name, "warehouses": warehouses,
        "rejected": rejected, "errors": errors or ([d.error] if d.error else []), "created_at": utc(d.created_at),
        "expires_at": utc(d.expires_at), "ozon_order_id": d.ozon_order_id,
    }


async def _sku_map(db: AsyncSession, items: list) -> dict[int, dict]:
    ids = [i.product_id for i in items]
    return {p.ozon_sku: {"id": p.id, "offer_id": p.offer_id} for p in (await db.execute(
        select(Product).where(Product.id.in_(ids)))).scalars() if p.ozon_sku}


async def _poll_info(client, d: SupplyDraft) -> None:
    for attempt in range(POLL_ATTEMPTS):
        info = await client.request("POST", DRAFT_INFO, {"draft_id": d.ozon_draft_id}) or {}
        status = info.get("status")
        if status in ("SUCCESS", "FAILED"):
            d.response_json = info
            d.status = DRAFT_READY if status == "SUCCESS" else DRAFT_FAILED
            if status == "FAILED" and not info.get("errors"):
                d.error = "Ozon не смог проверить план — проверьте состав и повторите"
            return
        if attempt < POLL_ATTEMPTS - 1:
            await asyncio.sleep(POLL_DELAY)


async def check(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> dict:
    """«Проверить в Ozon»: черновик заявки по плану (ничего не создаёт в кабинете)."""
    plan, items = await _plan_for_ozon(db, user_id, plan_id)
    skus = await _sku_map(db, items)
    by_product = {v["id"]: sku for sku, v in skus.items()}
    missing = [i for i in items if i.product_id not in by_product]
    if missing:
        raise PlanError("У части товаров нет SKU Ozon — обновите товары или уберите их из плана")
    now = _now()
    try:
        async with await get_ozon_client(user_id, db) as client:
            _, _, by_name = await fetch_clusters(client)
            cluster_id = by_name.get(cluster_key(plan.destination_name) or "") or by_name.get(plan.destination_key)
            if cluster_id is None:
                raise PlanError(f"Кластер «{plan.destination_name}» не найден в Ozon — создайте поставку в кабинете Ozon")
            lines = sorted(({"sku": by_product[i.product_id], "quantity": i.qty} for i in items), key=lambda x: x["sku"])
            body = {"cluster_info": {"items": lines,
                                     "macrolocal_cluster_id": cluster_id},
                    "deletion_sku_mode": "PARTIAL"}
            resp = await client.request("POST", DRAFT_CREATE, body) or {}
            draft = SupplyDraft(user_id=user_id, plan_id=plan.id, ozon_draft_id=resp.get("draft_id") or None,
                                cluster_id=cluster_id, status=DRAFT_PENDING, items_key=items_key(items), request_json=body,
                                response_json=resp, created_at=now, expires_at=now + DRAFT_TTL)
            if not draft.ozon_draft_id:
                draft.status = DRAFT_FAILED
                draft.error = "; ".join(_reasons([e.get("error_message") for e in resp.get("errors") or []])
                                        + [e.get("message") for e in resp.get("errors") or [] if e.get("message")]) \
                    or "Ozon не создал черновик — проверьте состав плана"
            else:
                await _poll_info(client, draft)
    except OzonAPIError as exc:
        raise _ozon_error(exc) from None
    db.add(draft)
    await db.commit()
    return draft_out(draft, skus)


async def _draft(db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> SupplyDraft:
    d = await db.get(SupplyDraft, draft_id)
    if d is None or d.user_id != user_id:
        raise PlanError("Проверка не найдена — проверьте план в Ozon заново", 404)
    return d


async def refresh(db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID) -> dict:
    """Черновик ещё проверяется — спросить Ozon снова."""
    d = await _draft(db, user_id, draft_id)
    items = [i for i in (await current_items(db, [d.plan_id]))[d.plan_id] if i.qty > 0]
    if d.status == DRAFT_PENDING and utc(d.expires_at) > _now():
        try:
            async with await get_ozon_client(user_id, db) as client:
                await _poll_info(client, d)
        except OzonAPIError as exc:
            raise _ozon_error(exc) from None
        await db.commit()
    return draft_out(d, await _sku_map(db, items))


def _usable(d: SupplyDraft) -> None:
    if d.status == DRAFT_USED:
        raise PlanError("По этой проверке заявка уже создавалась — проверьте план заново")
    if d.status != DRAFT_READY:
        raise PlanError("Ozon ещё не проверил план или проверка не удалась")
    if utc(d.expires_at) <= _now():
        raise PlanError("Проверка устарела: Ozon хранит черновик 30 минут — проверьте план заново")


async def timeslots(db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID, warehouse_id: int,
                    date_from: date | None = None, date_to: date | None = None) -> dict:
    d = await _draft(db, user_id, draft_id)
    _usable(d)
    today = today_msk()
    date_from = max(date_from or today, today)
    date_to = min(date_to or today + timedelta(days=TIMESLOT_MAX_DAYS - 1), today + timedelta(days=TIMESLOT_MAX_DAYS - 1))
    if date_to < date_from:
        raise PlanError("Период таймслотов — с сегодняшнего дня и не дальше 28 дней")
    body = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "draft_id": d.ozon_draft_id,
            "supply_type": SUPPLY_TYPE,
            "selected_cluster_warehouses": [{"macrolocal_cluster_id": d.cluster_id, "storage_warehouse_id": warehouse_id}]}
    try:
        async with await get_ozon_client(user_id, db) as client:
            resp = await client.request("POST", TIMESLOTS, body) or {}
    except OzonAPIError as exc:
        raise _ozon_error(exc) from None
    if resp.get("error_reason") not in (None, "UNSPECIFIED"):
        raise PlanError("Ozon не отдал таймслоты для этого склада — выберите другой склад")
    slots = ((resp.get("result") or {}).get("drop_off_warehouse_timeslots")) or {}
    days = [{"date": day.get("date_in_timezone"),
             "slots": [{"from": s.get("from_in_timezone"), "to": s.get("to_in_timezone")} for s in day.get("timeslots") or []
                       if s.get("from_in_timezone") and s.get("to_in_timezone")]}
            for day in slots.get("days") or []]
    return {"timezone": slots.get("warehouse_timezone"), "days": [d for d in days if d["slots"]]}


async def create(db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID, *, warehouse_id: int, slot_from: str,
                 slot_to: str, confirm: bool, request: Request | None = None) -> dict:
    """Создать заявку в Ozon по проверенному черновику — после подтверждения, один раз."""
    d = await _draft(db, user_id, draft_id)
    plan, items = await _plan_for_ozon(db, user_id, d.plan_id)
    _usable(d)
    if not confirm:
        raise PlanError("Подтвердите создание заявки: она появится в кабинете Ozon, и её нужно будет привезти в таймслот")
    if items_key(items) != d.items_key:
        raise PlanError("План изменили после проверки — проверьте его в Ozon заново")
    if warehouse_id not in {w["id"] for w in draft_out(d)["warehouses"] if w["available"]}:
        raise PlanError("Этот склад Ozon сейчас не принимает поставку — выберите другой")
    if not slot_from or not slot_to:
        raise PlanError("Выберите таймслот")
    body = {"draft_id": d.ozon_draft_id, "supply_type": SUPPLY_TYPE,
            "selected_cluster_warehouses": [{"macrolocal_cluster_id": d.cluster_id, "storage_warehouse_id": warehouse_id}],
            "timeslot": {"from_in_timezone": slot_from, "to_in_timezone": slot_to}}
    d.status, d.used_at = DRAFT_USED, _now()  # одна попытка: повтор — только по новой проверке
    await db.commit()
    before = {"plan_id": str(plan.id), "cluster": plan.destination_name, "items": len(items),
              "units": sum(i.qty for i in items)}
    status, error, order_id, resp, status_resp = FAILED, None, None, None, None
    try:
        async with await get_ozon_client(user_id, db) as client:
            resp = await client.request("POST", SUPPLY_CREATE, body) or {}
            reasons = _reasons(resp.get("error_reasons"))
            if reasons:
                status, error = REJECTED, "Ozon не создал заявку: " + "; ".join(reasons)
            else:
                for attempt in range(POLL_ATTEMPTS):
                    status_resp = await client.request("POST", SUPPLY_STATUS, {"draft_id": d.ozon_draft_id}) or {}
                    if status_resp.get("status") == "SUCCESS" and status_resp.get("order_id"):
                        status, order_id = APPLIED, int(status_resp["order_id"])
                        break
                    if status_resp.get("status") == "FAILED":
                        reasons = _reasons(status_resp.get("error_reasons"))
                        status, error = REJECTED, "Ozon не создал заявку: " + ("; ".join(reasons) or "причина не указана")
                        break
                    if attempt < POLL_ATTEMPTS - 1:
                        await asyncio.sleep(POLL_DELAY)
                else:
                    status, error = UNKNOWN, ("Ozon ещё создаёт заявку — она появится в списке заявок после синхронизации; "
                                              "проверьте кабинет Ozon, прежде чем создавать снова")
    except OzonTimeoutError:
        status, error = UNKNOWN, "Ozon не ответил вовремя — заявка могла создаться. Проверьте кабинет Ozon, прежде чем повторять"
    except OzonAPIError as exc:
        status, error = FAILED, _ozon_error(exc).message
    if order_id is not None:
        d.ozon_order_id = order_id
        await mark_ordered(db, plan, order_id)
    audit.record(db, user_id, action="create", entity_type=ENTITY, entity_id=str(plan.id), status=status,
                 before=before, after={"ozon_order_id": order_id, "warehouse_id": warehouse_id,
                                       "timeslot": {"from": slot_from, "to": slot_to}} if order_id else None,
                 ozon_request=body, ozon_response={"create": resp, "status": status_resp}, error=error, request=request)
    await db.commit()
    if status != APPLIED:
        raise PlanError(error or "Ozon не создал заявку", 502 if status in (FAILED, UNKNOWN) else 400)
    return {"plan_id": plan.id, "ozon_order_id": order_id,
            "message": "Заявка создана в Ozon. Грузоместа и пропуск водителя оформите в кабинете Ozon."}
