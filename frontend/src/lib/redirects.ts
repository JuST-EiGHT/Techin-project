// Старые адреса → новые (шаг 10.1, карта сервиса docs/SITEMAP.md, «Старые адреса → новые»).
// Закладки и ссылки в уже отправленных уведомлениях и в Telegram должны работать всегда — правила не удаляются.
// Применяет middleware.ts до проверки входа. Модуль без импортов: его же запускает pytest через node
// (backend/tests/test_sitemap.py) — проверяет каждый старый адрес.

export type Redirect = {
  from: string
  /** Только с этим ?tab= (вкладки «Планирования»); параметр tab в новый адрес не переносится. */
  tab?: string
  /** Только с этим параметром: его значение становится частью пути (/from?product=X → /to/X), сам параметр не переносится. */
  param?: string
  to: string
  /** Подстраницы тоже: /from/x → /to/x. */
  prefix?: boolean
  /** Временная (307): адрес ещё понадобится новой странице. */
  temporary?: boolean
}

export const REDIRECTS: Redirect[] = [
  // до шага 10.22 «Финансы» открываются с P&L; потом здесь будет «Обзор финансов» — переадресация временная
  { from: '/dashboard/finance', to: '/dashboard/finance/pnl', temporary: true },
  { from: '/dashboard/payouts', to: '/dashboard/finance/payouts' },
  { from: '/dashboard/reports', to: '/dashboard/analytics/reports', prefix: true },
  { from: '/dashboard/costs', to: '/dashboard/products/costs' },
  { from: '/dashboard/prices', to: '/dashboard/products/prices', prefix: true },
  { from: '/dashboard/promotions', to: '/dashboard/products/promotions', prefix: true },
  { from: '/dashboard/supply', to: '/dashboard/warehouses/supply' },
  // панель товара на «Всех товарах» (4.1) стала страницей «Статистика товара» (10.14) — ссылки уведомлений и Telegram
  { from: '/dashboard/products', param: 'product', to: '/dashboard/products' },
  // вкладки «Планирования» стали страницами: операции, статьи и счета — раздел «Операции», календарь и ДДС — свои страницы
  { from: '/dashboard/planning', tab: 'overview', to: '/dashboard/planning' },
  { from: '/dashboard/planning', tab: 'operations', to: '/dashboard/operations' },
  { from: '/dashboard/planning', tab: 'categories', to: '/dashboard/operations/categories' },
  { from: '/dashboard/planning', tab: 'accounts', to: '/dashboard/operations/accounts' },
  { from: '/dashboard/planning', tab: 'calendar', to: '/dashboard/planning/calendar' },
  { from: '/dashboard/planning', tab: 'cashflow', to: '/dashboard/planning/cashflow' },
  // меню аккаунта: «Магазины», «API-ключи Ozon», «Банки» — одна страница «Интеграции» с вкладками
  { from: '/dashboard/settings/stores', to: '/dashboard/settings/integrations' },
  { from: '/dashboard/settings/api-keys', to: '/dashboard/settings/integrations' },
  { from: '/dashboard/settings/banks', to: '/dashboard/settings/integrations?tab=banks' },
]

/** Куда переадресовать адрес (путь + параметры) или null. Параметры старого адреса сохраняются (кроме tab вкладки). */
export function resolveRedirect(pathname: string, search: string): { to: string; permanent: boolean } | null {
  const params = new URLSearchParams(search)
  for (const r of REDIRECTS) {
    const exact = pathname === r.from
    const sub = r.prefix && pathname.startsWith(`${r.from}/`)
    if (!exact && !sub) continue
    if (r.tab !== undefined && params.get('tab') !== r.tab) continue
    const segment = r.param !== undefined ? params.get(r.param) : null
    if (r.param !== undefined && !segment) continue
    const [path, query] = r.to.split('?')
    const out = new URLSearchParams(query)
    params.forEach((value, key) => {
      if (r.tab !== undefined && key === 'tab') return
      if (r.param !== undefined && key === r.param) return
      if (!out.has(key)) out.append(key, value)
    })
    const rest = segment ? `/${encodeURIComponent(segment)}` : sub ? pathname.slice(r.from.length) : ''
    const q = out.toString()
    return { to: `${path}${rest}${q ? `?${q}` : ''}`, permanent: !r.temporary }
  }
  return null
}
