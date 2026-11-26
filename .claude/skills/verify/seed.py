"""Тестовый магазин для проверки в браузере и замеров (skill verify): пользователь t1@example.com / password123,
40 товаров с длинными названиями, цены, остатки на 4 кластерах, начисления Ozon API за 120 дней (2–7 продаж в день,
привязаны к товарам), таблица продаж. Только для локальной базы ozon_e2e — не для сервера.

    cd backend && PYTHONPATH=. python ../.claude/skills/verify/seed.py
"""
import asyncio
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.api_credential import ApiCredential
from app.models.accrual import Accrual, AccrualDay
from app.models.product import Product, ProductPrice
from app.models.user import User
from app.models.warehouse import ProductStock, Warehouse
from app.services import sales
from app.services.encryption import encrypt

EMAIL, PASSWORD = "t1@example.com", "password123"
NAMES = ["Подшипник шариковый радиальный однорядный 6204 2RS C3 20x47x14 NACHI Таиланд", "Подшипник 6201 12x32x10 NACHI",
         "Подшипник кондиционера автомобильного компрессора ACB355222 NACHI Япония оригинал", "Подшипник 6000 2RS 10x26x8"]
CLUSTERS = [("Казань", "КАЗАНЬ_РФЦ"), ("Ростов", "РОСТОВ_РФЦ"), ("Москва, МО и Дальние регионы", "ХОРУГВИНО_РФЦ"),
            ("Красноярск", "КРАСНОЯРСК_РФЦ")]


async def main() -> None:
    # пользователь — через API (регистрация открыта: REGISTRATION_MODE=open), уже есть — не страшно
    httpx.post("http://localhost:8000/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Тест"})
    random.seed(7)
    now, today = datetime.now(timezone.utc), date.today()
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        if (await db.execute(select(Product.id).where(Product.user_id == user.id).limit(1))).first():
            print("товары уже есть — пропускаю")
            return
        whs = []
        for i, (cluster, name) in enumerate(CLUSTERS):
            w = Warehouse(user_id=user.id, ozon_warehouse_id=1000 + i, name=name, type="FBO", cluster_id=10 + i,
                          cluster_name=cluster, macrolocal_cluster_id=10 + i, synced_at=now)
            db.add(w)
            whs.append(w)
        products = []
        for n in range(40):
            p = Product(user_id=user.id, ozon_product_id=500000 + n, ozon_sku=3145300000 + n,
                        offer_id=f"{6200 + n}-2RS.C3.SRL.NAC.TH" if n % 3 else f"ACB{355200 + n}.NAC.JP",
                        name=NAMES[n % len(NAMES)] + (f" вариант {n}" if n >= 4 else ""), barcode=f"46000000{n:05d}",
                        vat=Decimal("0.22"), synced_at=now)
            db.add(p)
            await db.flush()
            products.append(p)
            price = Decimal(random.randint(400, 4000))
            db.add(ProductPrice(product_id=p.id, snapshot_at=now, price=price, min_price=price * Decimal("0.8"),
                                old_price=price * 2, marketing_price=price * Decimal("0.95"), commission_pct=Decimal("19.5"),
                                price_index=random.choice(["GREEN", "YELLOW", "RED", None])))
            for w in whs:
                if random.random() < 0.7:
                    db.add(ProductStock(product_id=p.id, warehouse_id=w.id, snapshot_at=now, present=random.randint(0, 120),
                                        reserved=None, in_transit=random.choice([0, 0, 5, 12])))
        await db.execute(delete(Accrual).where(Accrual.user_id == user.id))
        for d in range(120):
            day, k = today - timedelta(days=d + 1), 0
            for _ in range(random.randint(2, 7)):
                p = random.choice(products)
                post, price = f"{random.randint(10**7, 10**8)}-0001-1", Decimal(random.randint(500, 3500))
                for t, amount, qty in (("Выручка", price, 1), ("Вознаграждение за продажу", -price * Decimal("0.19"), None),
                                       ("Логистика", Decimal(-random.randint(60, 140)), None), ("Последняя миля", Decimal(-25), None)):
                    k += 1
                    db.add(Accrual(user_id=user.id, product_id=p.id, posting_number=post, sku=p.ozon_sku, type_name=t,
                                   accrual_date=day, amount=amount, quantity=qty, source="api", sync_scope=f"day:{day}",
                                   line_no=k, raw_json={"operation": "x" * 1500}))
            k += 1
            db.add(Accrual(user_id=user.id, type_name="Размещение товаров на складах", accrual_date=day, amount=Decimal(-40),
                           source="api", sync_scope=f"day:{day}", line_no=k))
            db.add(AccrualDay(user_id=user.id, day=day, synced_at=now, entries=k))
        # ненастоящий ключ Ozon: главная и разделы показывают данные, как у магазина с ключом (в Ozon не ходим —
        # синхронизация с ним упадёт с ошибкой ключа, проверкам интерфейса это не мешает)
        db.add(ApiCredential(user_id=user.id, label="Тестовый ключ", client_id="000000", api_key_encrypted=encrypt("test"),
                             is_active=True, expires_at=now + timedelta(days=90)))
        await db.commit()
        await sales.rebuild_all(db, user.id)
        await db.commit()
    print("готово: 40 товаров, начисления за 120 дней")


asyncio.run(main())
