// Меню кабинета — один конфиг на шапку, мобильное меню и меню аккаунта (шаг 0.5).
// Страница попадает сюда, только когда она сделана: новый шаг добавляет свой пункт.
// Ещё появятся: Финансы — P&L по товарам (3.4), выплаты Ozon (3.6);
// Планирование (необязательный раздел) — обзор и ДДС (3.10), операции и счета (3.8), календарь (3.9);
// Товары — товары (4.1), цены (4.2), история цен (6.4); Акции — история участия (4.3);
// Склады — кандидаты на вывоз (5.1), поставки (5.2).
// Меню аккаунта — банки (3.12), уведомления (7.3), тариф (8.1).

export type NavItem = { href: string; label: string; description?: string }
export type NavSection = { label: string; href?: string; items?: NavItem[] }

export const NAV: NavSection[] = [
  { label: 'Главная', href: '/dashboard' },
  {
    label: 'Финансы',
    items: [
      { href: '/dashboard/finance', label: 'P&L — прибыль', description: 'Выручка, расходы Ozon, налог и остаток за период' },
      { href: '/dashboard/reports', label: 'Отчёты Ozon', description: 'Недельные и месячные отчёты, история' },
      { href: '/dashboard/reports/upload', label: 'Загрузить отчёт', description: 'XLSX из личного кабинета Ozon' },
      { href: '/dashboard/costs', label: 'Себестоимость', description: 'Цены закупки по артикулам с историей' },
    ],
  },
  {
    label: 'Акции',
    items: [
      { href: '/dashboard/promotions', label: 'Все акции', description: 'Участие, товары и маржа в акции' },
      {
        href: '/dashboard/promotions?tab=auto',
        label: 'Добавленные автоматически',
        description: 'Товары, которые Ozon добавил в акции без вас',
      },
    ],
  },
  {
    label: 'Склады',
    items: [{ href: '/dashboard/warehouses', label: 'Остатки по складам', description: 'FBO по кластерам, FBS и rFBS' }],
  },
]

export const ACCOUNT_MENU: NavItem[] = [
  { href: '/dashboard/settings/account', label: 'Аккаунт' },
  { href: '/dashboard/settings/api-keys', label: 'API-ключи Ozon' },
  { href: '/dashboard/settings/taxes', label: 'Налоги и НДС' },
  { href: '/dashboard/settings', label: 'Основные настройки' },
]

const path = (href: string) => href.split('?')[0]

/** Пункт относится к текущей странице: тот же путь или его подстраница. */
export function matches(href: string, pathname: string): boolean {
  const p = path(href)
  return pathname === p || (p !== '/dashboard' && pathname.startsWith(`${p}/`))
}

export function sectionActive(section: NavSection, pathname: string): boolean {
  if (section.href) return matches(section.href, pathname)
  return (section.items ?? []).some((i) => matches(i.href, pathname))
}
