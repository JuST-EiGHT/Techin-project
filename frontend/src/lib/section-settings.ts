// Шестерёнка в шапке (правка владельца 06.11.2026): у каждого раздела — свои настройки, одним нажатием.
// Раздел → группа «Настроек» (якоря групп — app/dashboard/settings/page.tsx, GROUPS). Длинный префикс важнее.

export type SectionSettings = { href: string; title: string }

const MAP: { prefix: string; group: string; title: string; exact?: boolean }[] = [
  { prefix: '/dashboard', group: 'analytics', title: 'Главная: советы и рекомендации', exact: true },
  { prefix: '/dashboard/analytics/abc', group: 'analytics', title: 'ABC-анализ: границы групп' },
  { prefix: '/dashboard/analytics', group: 'profit', title: 'Аналитика: порог низкой маржи' },
  { prefix: '/dashboard/products/prices', group: 'prices', title: 'Цены: расчёт минимальной и целевой цены' },
  { prefix: '/dashboard/products/promotions', group: 'prices', title: 'Акции: расчёт маржи в акции' },
  { prefix: '/dashboard/products/costs', group: 'accounting', title: 'Себестоимость: как учитывать закупку' },
  { prefix: '/dashboard/products', group: 'profit', title: 'Товары: маржа и прибыль с продажи' },
  { prefix: '/dashboard/warehouses/supply', group: 'supply', title: 'Поставки: срок, страховой запас, период' },
  { prefix: '/dashboard/warehouses', group: 'stock', title: 'Склады: пороги остатков' },
  { prefix: '/dashboard/operations', group: 'planning', title: 'Операции: счета, остатки, ДДС' },
  { prefix: '/dashboard/planning', group: 'planning', title: 'Планирование: прогноз и кассовый разрыв' },
  { prefix: '/dashboard/finance', group: 'accounting', title: 'Финансы: налоги и учёт закупки' },
]

/** Настройки раздела, на странице которого пользователь; null — у страницы своих настроек нет (сами «Настройки»). */
export function sectionSettings(pathname: string): SectionSettings | null {
  if (pathname.startsWith('/dashboard/settings')) return null
  if (pathname.startsWith('/dashboard/alerts')) return { href: '/dashboard/settings/notifications', title: 'Уведомления: что и куда присылать' }
  const hit = MAP.filter((m) => (m.exact ? pathname === m.prefix : pathname === m.prefix || pathname.startsWith(`${m.prefix}/`))).sort(
    (a, b) => b.prefix.length - a.prefix.length,
  )[0]
  if (!hit) return null
  return { href: `/dashboard/settings?from=${encodeURIComponent(pathname)}#${hit.group}`, title: hit.title }
}

/** Безопасный адрес «вернуться» на странице настроек: только внутренний путь кабинета. */
export function backPath(from: string | null): string | null {
  if (!from || !from.startsWith('/dashboard') || from.startsWith('//') || from.includes('\\')) return null
  return from
}
