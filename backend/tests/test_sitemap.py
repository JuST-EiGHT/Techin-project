"""Шаг 10.1: новое меню и переезд разделов по карте сервиса (docs/SITEMAP.md).

Старые адреса переадресуются навсегда — закладки и ссылки в уже отправленных уведомлениях и в Telegram работают.
Переадресацию делает frontend/src/lib/redirects.ts (его вызывает middleware сайта): тест запускает ту же функцию через
node — Node 22 исполняет TypeScript без сборки. Ссылки аномалий, календаря и Telegram ведут сразу на новые адреса, пункты
меню — на существующие страницы.
"""
import json
import re
import shutil
import subprocess
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine import anomaly_detector as det
from app.engine.anomaly_text import describe
from app.services import telegram_bot

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend" / "src"
APP = FRONT / "app"
REDIRECTS = FRONT / "lib" / "redirects.ts"
NAV = FRONT / "lib" / "nav.ts"

pytestmark = pytest.mark.skipif(not REDIRECTS.exists(), reason="нет исходников фронтенда рядом (тесты в контейнере api)")

ID = "3f2b8c1e-5d4a-4e6b-9c7d-1a2b3c4d5e6f"

# старый адрес → (новый адрес, навсегда ли); параметры вкладок и фильтров переносятся
OLD_TO_NEW = {
    # старые ссылки P&L (3.3 жил на /dashboard/finance): с параметрами P&L — в P&L; без них — «Обзор финансов» (10.22)
    "/dashboard/finance?tab=products": ("/dashboard/finance/pnl?tab=products", True),
    "/dashboard/finance?period=custom&from=2026-10-01&to=2026-10-07&tab=products":
        ("/dashboard/finance/pnl?period=custom&from=2026-10-01&to=2026-10-07&tab=products", True),
    f"/dashboard/finance?report={ID}": (f"/dashboard/finance/pnl?report={ID}", True),
    "/dashboard/payouts": ("/dashboard/finance/payouts", True),
    "/dashboard/payouts?week=2026-10-06": ("/dashboard/finance/payouts?week=2026-10-06", True),
    "/dashboard/reports": ("/dashboard/analytics/reports", True),
    "/dashboard/reports?tab=realization": ("/dashboard/analytics/reports?tab=realization", True),
    f"/dashboard/reports?deleted={ID}&tab=realization": (f"/dashboard/analytics/reports?deleted={ID}&tab=realization", True),
    "/dashboard/reports/upload": ("/dashboard/analytics/reports/upload", True),
    f"/dashboard/reports/{ID}": (f"/dashboard/analytics/reports/{ID}", True),
    "/dashboard/costs": ("/dashboard/products/costs", True),
    "/dashboard/costs?missing=1": ("/dashboard/products/costs?missing=1", True),
    "/dashboard/prices": ("/dashboard/products/prices", True),
    "/dashboard/prices/bulk": ("/dashboard/products/prices/bulk", True),
    "/dashboard/prices/history": ("/dashboard/products/prices/history", True),
    f"/dashboard/prices/history?product={ID}": (f"/dashboard/products/prices/history?product={ID}", True),
    "/dashboard/promotions": ("/dashboard/products/promotions", True),
    "/dashboard/promotions?tab=auto": ("/dashboard/products/promotions?tab=auto", True),
    f"/dashboard/promotions?tab=history&product={ID}": (f"/dashboard/products/promotions?tab=history&product={ID}", True),
    "/dashboard/promotions/1234567": ("/dashboard/products/promotions/1234567", True),
    "/dashboard/supply": ("/dashboard/warehouses/supply", True),
    # панель товара (4.1) → страница «Статистика товара» (10.14); фильтры списка не переносятся в путь
    f"/dashboard/products?product={ID}": (f"/dashboard/products/{ID}", True),
    f"/dashboard/products?filter=no_cost&product={ID}&store={ID}": (f"/dashboard/products/{ID}?filter=no_cost&store={ID}", True),
    # вкладки «Планирования» — страницы: tab вкладки в новый адрес не переносится, фильтры — переносятся
    "/dashboard/planning?tab=overview": ("/dashboard/planning", True),
    "/dashboard/planning?tab=operations": ("/dashboard/operations", True),
    "/dashboard/planning?tab=operations&category=none&from=2026-09-01&to=2026-10-15":
        ("/dashboard/operations?category=none&from=2026-09-01&to=2026-10-15", True),
    "/dashboard/planning?tab=categories": ("/dashboard/operations/categories", True),
    "/dashboard/planning?tab=accounts": ("/dashboard/operations/accounts", True),
    "/dashboard/planning?tab=calendar": ("/dashboard/planning/calendar", True),
    "/dashboard/planning?tab=calendar&day=2026-10-17": ("/dashboard/planning/calendar?day=2026-10-17", True),
    "/dashboard/planning?tab=calendar&plan=%D0%90%D1%80%D0%B5%D0%BD%D0%B4%D0%B0":
        ("/dashboard/planning/calendar?plan=%D0%90%D1%80%D0%B5%D0%BD%D0%B4%D0%B0", True),
    "/dashboard/planning?tab=cashflow": ("/dashboard/planning/cashflow", True),
    # ссылки из Telegram у аккаунта с несколькими магазинами (шаг 8.4b): магазин доезжает до новой страницы
    f"/dashboard/planning?tab=calendar&store={ID}": (f"/dashboard/planning/calendar?store={ID}", True),
    f"/dashboard/planning?tab=operations&category=none&from=2026-09-01&to=2026-10-15&store={ID}":
        (f"/dashboard/operations?category=none&from=2026-09-01&to=2026-10-15&store={ID}", True),
    f"/dashboard/payouts?store={ID}": (f"/dashboard/finance/payouts?store={ID}", True),
    f"/dashboard/promotions/1234567?store={ID}": (f"/dashboard/products/promotions/1234567?store={ID}", True),
    # меню аккаунта: «Магазины», «API-ключи Ozon», «Банки» — «Интеграции» с вкладками
    "/dashboard/settings/stores": ("/dashboard/settings/integrations", True),
    "/dashboard/settings/api-keys": ("/dashboard/settings/integrations", True),
    "/dashboard/settings/banks": ("/dashboard/settings/integrations?tab=banks", True),
}

# эти адреса — на своём месте: переадресации нет
STAYS = [
    "/dashboard", "/dashboard?period=week", "/dashboard/planning", "/dashboard/products", "/dashboard/products?filter=no_cost", f"/dashboard/products/{ID}",
    "/dashboard/products/prices", "/dashboard/pricesx", "/dashboard/reportsx/1", "/dashboard/finance/pnl",
    "/dashboard/finance", "/dashboard/finance?period=week",
    "/dashboard/settings/notifications", "/dashboard/settings", "/dashboard/alerts", "/dashboard/warehouses/withdrawal",
]

# старые адреса в исходниках: только в таблице переадресации
OLD_IN_SOURCE = re.compile(
    r"/dashboard/(payouts|reports|costs|prices|promotions|supply)\b|/dashboard/planning\?tab|"
    r"/dashboard/settings/(stores|api-keys|banks)\b|/dashboard/finance\?(report|tab)=|/dashboard/products\?product="
)

SCRIPT = """
const read = async () => { let s = ''; for await (const c of process.stdin) s += c; return JSON.parse(s) }
const { files, urls } = await read()
const { resolveRedirect, REDIRECTS } = await import(files.redirects)
const { NAV, ACCOUNT_MENU } = await import(files.nav)
const resolved = Object.fromEntries(urls.map((u) => {
  const [path, query] = u.split('?')
  return [u, resolveRedirect(path, query ? `?${query}` : '')]
}))
const items = NAV.flatMap((s) => [s.href, ...(s.items ?? []).flatMap((i) => [i.href, ...(i.children ?? []).map((c) => c.href)])])
console.log(JSON.stringify({ resolved, rules: REDIRECTS, nav: [...items.filter(Boolean), ...ACCOUNT_MENU.map((i) => i.href)] }))
"""


def run_node(urls: list[str]) -> dict:
    """Функция переадресации сайта и меню — через node (TypeScript без сборки: Node 22.6+)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен — переадресацию проверяет только браузерная проверка")
    payload = json.dumps({"files": {"redirects": REDIRECTS.as_uri(), "nav": NAV.as_uri()}, "urls": urls})
    for flags in ([], ["--experimental-strip-types"]):
        r = subprocess.run([node, *flags, "--no-warnings", "--input-type=module", "-e", SCRIPT], input=payload,
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return json.loads(r.stdout)
    pytest.skip(f"node не исполняет TypeScript (нужен Node 22.6+): {r.stderr.strip()[:200]}")


def page_exists(url: str) -> bool:
    """Есть страница Next по адресу: каталоги app/…/page.tsx, вместо сегмента — [параметр]."""
    here = APP
    for part in url.split("?")[0].strip("/").split("/"):
        if (here / part).is_dir():
            here = here / part
            continue
        dynamic = [d for d in here.iterdir() if d.is_dir() and d.name.startswith("[")]
        if not dynamic:
            return False
        here = dynamic[0]
    return (here / "page.tsx").exists()


@pytest.fixture(scope="module")
def site() -> dict:
    return run_node([*OLD_TO_NEW, *STAYS])


def test_every_old_address_redirects_to_new_place(site):
    for old, (new, permanent) in OLD_TO_NEW.items():
        got = site["resolved"][old]
        assert got == {"to": new, "permanent": permanent}, old
        assert page_exists(new), new
        # новый адрес — конечный: без цепочек переадресаций
        assert run_node([new])["resolved"][new] is None, new


def test_current_addresses_are_not_redirected(site):
    for url in STAYS:
        assert site["resolved"][url] is None, url


def test_old_pages_moved_not_duplicated(site):
    # старая страница не осталась рядом с новой (кроме /dashboard/planning — там остался «Обзор», уходят только вкладки;
    # и /dashboard/finance — там «Обзор финансов», уходят только ссылки с параметрами P&L)
    for rule in site["rules"]:
        if "tab" in rule or "param" in rule or "query" in rule:  # вкладка или параметр — сама страница остаётся
            continue
        assert not page_exists(rule["from"]), rule["from"]
        assert page_exists(rule["to"]), rule["to"]


def test_menu_items_are_pages(site):
    assert site["nav"], "меню пустое"
    resolved = run_node(site["nav"])["resolved"]
    for href in site["nav"]:
        assert page_exists(href), href
        assert resolved[href] is None, f"пункт меню ведёт на старый адрес: {href}"


def anomaly_links() -> tuple[dict[str, str | None], set[str]]:
    pid = uuid.UUID(ID)
    today = date(2026, 10, 15)
    cases = {
        "sales_shop": (det.SALES_DROP, det.YELLOW, None, {"change_pct": -50, "actual": 1, "expected": 5}),
        "sales_product": (det.SALES_GROWTH, det.YELLOW, pid, {"change_pct": 80, "actual": 9, "expected": 5}),
        "price_service": (det.PRICE_CHANGE, det.RED, pid, {"change_pct": -40, "via_service": True}),
        "price_ozon": (det.PRICE_CHANGE, det.YELLOW, pid, {"change_pct": 15, "via_service": False}),
        "commission": (det.COMMISSION_CHANGE, det.YELLOW, pid, {"change_pp": 2}),
        "low_margin": (det.LOW_MARGIN, det.RED, pid, {"price": "990"}),
        "low_stock": (det.LOW_STOCK, det.RED, pid, {"available": 3, "avg_daily": "1.5"}),
        "promo_auto": (det.PROMO_AUTO_ADDED, det.YELLOW, pid, {"promotion": "Распродажа", "promotion_id": "1234567"}),
        "promo_auto_no_id": (det.PROMO_AUTO_ADDED, det.YELLOW, pid, {"promotion": "Распродажа"}),
        "promo_loss": (det.PROMO_LOSS, det.RED, pid, {"promotion": "Распродажа", "promotion_id": "1234567"}),
        "payout_missed": (det.PAYOUT_MISSED, det.RED, None, {"day": "2026-10-07"}),
        "payout_diff": (det.PAYOUT_DIFF, det.YELLOW, None, {"day": "2026-10-07", "change_pct": 20}),
        "bank_missing": (det.BANK_MISSING, det.RED, None, {"day": "2026-10-07"}),
        "bank_diff": (det.BANK_DIFF, det.YELLOW, None, {"day": "2026-10-07", "bank_day": "2026-10-08"}),
        "cash_gap": (det.CASH_GAP, det.RED, None, {"first_day": "2026-10-17", "min_day": "2026-10-20"}),
        "uncategorized": (det.UNCATEGORIZED, det.YELLOW, None, {"oldest_day": "2026-09-01"}),
        "uncategorized_no_day": (det.UNCATEGORIZED, det.YELLOW, None, {}),
        # шаг 10.5: избыток и проверки данных
        "excess_stock": (det.EXCESS_STOCK, det.YELLOW, pid, {"available": 300, "avg_daily": "2", "excess_qty": 180}),
        "sync_key": (det.SYNC_PROBLEM, det.RED, None, {"titles": ["Остатки"], "problem": "Ozon не принял ключ",
                                                       "href": "/dashboard/settings/integrations"}),
        "sync_stale": (det.SYNC_PROBLEM, det.YELLOW, None, {"titles": ["Остатки"], "problem": "Давно не обновлялись"}),
        "accrual_gaps": (det.ACCRUAL_GAPS, det.YELLOW, None, {"count": 2, "ranges": [["2026-10-05", "2026-10-06"]]}),
        "report_error": (det.REPORT_ERROR, det.YELLOW, None, {"filename": "sept.xlsx"}),
        "missing_cost": (det.MISSING_COST, det.YELLOW, None, {"count": 1, "revenue": "500", "top": []}),
    }
    links = {
        name: describe(t, sev, Decimal(100), Decimal(50), Decimal(10), d, pid_, "Подшипник 6001", today).link
        for name, (t, sev, pid_, d) in cases.items()
    }
    return links, {t for t, *_ in cases.values()}


def test_anomaly_links_lead_to_new_pages():
    links, types = anomaly_links()
    assert types == set(det.TYPES)  # все 19 проверок (14 — шаг 7.1, 5 — шаг 10.5)
    assert links["sales_shop"] == "/dashboard/finance/pnl?tab=products"
    assert links["price_service"] == f"/dashboard/products/prices/history?product={ID}"
    assert links["promo_auto"] == "/dashboard/products/promotions/1234567"
    assert links["promo_auto_no_id"] == "/dashboard/products/promotions"
    assert links["payout_missed"] == links["payout_diff"] == "/dashboard/finance/payouts"
    assert links["bank_missing"] == links["bank_diff"] == "/dashboard/operations/accounts"
    assert links["cash_gap"] == "/dashboard/planning/calendar"
    assert links["uncategorized"] == "/dashboard/operations?category=none&from=2026-09-01&to=2026-10-15"
    assert links["uncategorized_no_day"] == "/dashboard/operations"
    assert links["excess_stock"] == "/dashboard/warehouses/withdrawal"
    assert links["sync_key"] == "/dashboard/settings/integrations"
    assert links["sync_stale"] == "/dashboard?sync=1"  # открывает окно «Синхронизация»
    assert links["accrual_gaps"] == links["report_error"] == "/dashboard/analytics/reports"
    assert links["missing_cost"] == "/dashboard/products/costs?missing=1"
    # товар — страница «Статистика товара» (10.14), а не панель
    assert links["low_stock"] == links["sales_product"] == links["low_margin"] == f"/dashboard/products/{ID}"

    urls = [u for u in links.values() if u]
    resolved = run_node(urls)["resolved"]
    for name, url in links.items():
        assert url, name
        assert page_exists(url), (name, url)
        assert resolved[url] is None, f"{name}: ссылка на старый адрес {url}"


def test_service_links_lead_to_new_pages():
    # Telegram: где получить код подключения; календарь: выплаты Ozon; уведомления: все и настройки
    urls = [telegram_bot.LINK_PAGE, "/dashboard/finance/payouts", "/dashboard/alerts", "/dashboard/settings/notifications"]
    resolved = run_node(urls)["resolved"]
    for url in urls:
        assert page_exists(url) and resolved[url] is None, url
    assert telegram_bot.LINK_PAGE == "/dashboard/settings/integrations?tab=telegram"


def test_no_old_addresses_in_source():
    # все ссылки — сразу на новые адреса; старые остаются только в таблице переадресации
    sources = [p for p in FRONT.rglob("*") if p.suffix in (".ts", ".tsx") and p != REDIRECTS]
    sources += list((ROOT / "backend" / "app").rglob("*.py"))
    found = [f"{p.relative_to(ROOT)}:{i}: {line.strip()}" for p in sources
             for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if OLD_IN_SOURCE.search(line)]
    assert found == []
