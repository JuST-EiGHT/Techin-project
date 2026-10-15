// Меню кабинета — один конфиг на шапку, мобильное меню и меню аккаунта (шаг 0.5).
// Страница попадает сюда, только когда она сделана: новый шаг добавляет свой пункт.
// Ещё появятся: Товары — цены (4.2), история цен (6.4); Акции — история участия (4.3);
// Склады — кандидаты на вывоз (5.1), поставки (5.2).
// Меню аккаунта — уведомления (7.3), тариф (8.1).

export type NavItem = { href: string; label: string; description?: string }
export type NavSection = { label: string; href?: string; items?: NavItem[] }

export const NAV: NavSection[] = [
  { label: 'Главная', href: '/dashboard' },
  {
    label: 'Товары',
    items: [
      {
        href: '/dashboard/products',
        label: 'Все товары',
        description: 'Цена, комиссия, себестоимость, маржа, остаток и акции каждого товара',
      },
    ],
  },
  {
    label: 'Финансы',
    items: [
      { href: '/dashboard/finance', label: 'P&L — прибыль', description: 'Выручка, расходы Ozon, налог и остаток за период' },
      { href: '/dashboard/finance?tab=products', label: 'Прибыль по товарам', description: 'Маржа каждого товара, комиссия, логистика' },
      { href: '/dashboard/payouts', label: 'Выплаты Ozon', description: 'Когда и сколько придёт, баланс у Ozon, график выплат' },
      { href: '/dashboard/reports', label: 'Отчёты Ozon', description: 'Недельные и месячные отчёты, история' },
      { href: '/dashboard/reports/upload', label: 'Загрузить отчёт', description: 'XLSX из личного кабинета Ozon' },
      { href: '/dashboard/costs', label: 'Себестоимость', description: 'Цены закупки по артикулам с историей' },
    ],
  },
  {
    // необязательный раздел (шаг 3.8): пока не включён, пункты ведут на страницу-приглашение
    label: 'Планирование',
    items: [
      { href: '/dashboard/planning', label: 'Обзор', description: 'Деньги сейчас, прогноз остатка, кассовые разрывы' },
      { href: '/dashboard/planning?tab=operations', label: 'Операции', description: 'Что пришло и что ушло: свои расходы, доходы, выплаты Ozon' },
      { href: '/dashboard/planning?tab=calendar', label: 'Календарь', description: 'Плановые платежи, налоги и прогноз остатка — хватит ли денег' },
      { href: '/dashboard/planning?tab=cashflow', label: 'ДДС', description: 'Движение денег по статьям за месяц или неделю' },
      { href: '/dashboard/planning?tab=accounts', label: 'Счета', description: 'Счета, карты и наличные — сколько денег сейчас' },
      { href: '/dashboard/planning?tab=categories', label: 'Статьи', description: 'Статьи доходов и расходов' },
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
  { href: '/dashboard/settings/banks', label: 'Банки' },
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
