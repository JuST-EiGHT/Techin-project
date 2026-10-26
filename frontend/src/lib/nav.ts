// Меню кабинета — один конфиг на шапку, мобильное меню, меню аккаунта и вкладки разделов (шаги 0.5, 10.1).
// Порядок и адреса — по карте сервиса docs/SITEMAP.md. Страница попадает сюда, только когда она сделана: новый шаг
// добавляет свой пункт (обзоры аналитики и финансов, бюджеты — шаги фазы 10; «Автоматизация» — 9.1; тариф — 8.1).
// Модуль без импортов: pytest (backend/tests/test_sitemap.py) проверяет, что каждый пункт — существующая страница.

export type NavItem = {
  href: string
  label: string
  description?: string
  /** Подстраницы пункта — мелкими ссылками под ним (у «Цен» — «Списком» и «История»). */
  children?: NavItem[]
}
export type NavSection = { label: string; href?: string; items?: NavItem[]; bellOnDesktop?: boolean }

export const NAV: NavSection[] = [
  { label: 'Главная', href: '/dashboard' },
  {
    label: 'Аналитика',
    items: [
      {
        href: '/dashboard/analytics',
        label: 'Обзор бизнеса',
        description: 'Выручка, прибыль, маржа и продажи к прошлому периоду, лучшие и худшие товары',
      },
      {
        href: '/dashboard/analytics/sales',
        label: 'Аналитика продаж',
        description: 'Заказы, продажи, возвраты и средняя цена по дням, товарам и с событиями — цена, акции, остатки',
      },
      {
        href: '/dashboard/analytics/profit',
        label: 'Аналитика прибыльности',
        description: 'Выручка, расходы и прибыль, на что ушли деньги, товары в минус и с низкой маржой — до начислений Ozon',
      },
      {
        href: '/dashboard/analytics/abc',
        label: 'ABC-анализ',
        description: 'Какие товары дают основную выручку, прибыль или продажи — группы A, B, C и их сравнение',
      },
      {
        href: '/dashboard/analytics/events',
        label: 'История событий',
        description: 'Цены, акции, остатки, аномалии, ваши действия и ошибки загрузки — одной лентой',
      },
      {
        href: '/dashboard/analytics/reports',
        label: 'Отчёты Ozon и загрузка',
        description: 'Недельные и месячные отчёты Ozon: загрузить XLSX, история, P&L по отчёту',
      },
    ],
  },
  {
    label: 'Товары',
    items: [
      {
        href: '/dashboard/products',
        label: 'Все товары',
        description: 'Цена, комиссия, себестоимость, маржа, остаток и акции каждого товара',
      },
      {
        href: '/dashboard/products/prices',
        label: 'Цены',
        description: 'Минимальная и целевая цена, маржа сейчас и в акции, что поднять',
        children: [
          { href: '/dashboard/products/prices/bulk', label: 'Цены списком', description: 'По стратегии или из Excel' },
          { href: '/dashboard/products/prices/history', label: 'История цен', description: 'Что меняли и откат за 7 дней' },
        ],
      },
      { href: '/dashboard/products/costs', label: 'Себестоимость', description: 'Цены закупки по артикулам с историей, шаблон Excel' },
      {
        href: '/dashboard/products/promotions',
        label: 'Акции',
        description: 'Маржа в акции, вход и выход, добавленные Ozon сами, история участия',
      },
    ],
  },
  {
    label: 'Склады',
    items: [
      {
        href: '/dashboard/warehouses',
        label: 'Остатки',
        description: 'По товарам, складам и кластерам: что заканчивается, что лежит, сколько денег в запасе',
      },
      {
        href: '/dashboard/warehouses/supply',
        label: 'Поставки',
        description: 'Что и куда везти, планы поставок, заявки Ozon со статусами и календарь',
      },
      {
        href: '/dashboard/warehouses/withdrawal',
        label: 'На вывоз',
        description: 'Что, откуда и сколько забрать со складов Ozon, сколько денег заморожено — решения и Excel',
      },
    ],
  },
  {
    // «Операции» и «Планирование» — необязательные (шаг 3.8): пока не включены, страницы показывают приглашение.
    // Пункты этих разделов — и вкладки на их страницах (components/planning/Shell.tsx)
    label: 'Операции',
    items: [
      { href: '/dashboard/operations', label: 'Все операции', description: 'Что пришло и что ушло: свои расходы, доходы, выплаты Ozon' },
      { href: '/dashboard/operations/categories', label: 'Статьи', description: 'Статьи доходов и расходов' },
      { href: '/dashboard/operations/rules', label: 'Правила разнесения', description: 'Операции из банка сами получают статью' },
      { href: '/dashboard/operations/budgets', label: 'Бюджеты и сценарии', description: 'План по статьям на месяц, квартал, год, план-факт, сценарии' },
      { href: '/dashboard/operations/accounts', label: 'Счета и выписки', description: 'Счета, карты и наличные, остатки, выписки банков' },
    ],
  },
  {
    label: 'Финансы',
    items: [
      { href: '/dashboard/finance', label: 'Обзор финансов', description: 'Выручка, расходы, прибыль, деньги на Ozon и на счетах, риски' },
      { href: '/dashboard/finance/pnl', label: 'P&L — прибыль', description: 'Выручка, расходы Ozon и бизнеса, налог и чистая прибыль' },
      { href: '/dashboard/finance/pnl?tab=products', label: 'Прибыль по товарам', description: 'Маржа каждого товара, комиссия, логистика' },
      { href: '/dashboard/finance/payouts', label: 'Выплаты и расчёты', description: 'Когда и сколько придёт, начисления, сверка с банком, расхождения' },
      { href: '/dashboard/finance/documents', label: 'Отчёты и документы', description: 'Отчёты Ozon о начислениях и реализации, загрузка' },
    ],
  },
  {
    label: 'Планирование',
    items: [
      { href: '/dashboard/planning', label: 'Обзор', description: 'Деньги сейчас, прогноз остатка, кассовые разрывы' },
      { href: '/dashboard/planning/calendar', label: 'Календарь', description: 'Плановые платежи, налоги и прогноз остатка — хватит ли денег' },
      { href: '/dashboard/planning/cashflow', label: 'ДДС', description: 'Движение денег по статьям за месяц или неделю' },
    ],
  },
  // шаг 7.2: в шапке — колокольчик со счётчиком, поэтому на широком экране пункт текстом не дублируется (bellOnDesktop)
  { label: 'Уведомления', href: '/dashboard/alerts', bellOnDesktop: true },
]

export const ACCOUNT_MENU: NavItem[] = [
  { href: '/dashboard/settings/account', label: 'Профиль и безопасность' },
  { href: '/dashboard/settings/integrations', label: 'Интеграции' },
  { href: '/dashboard/settings/taxes', label: 'Налоги и учёт' },
  { href: '/dashboard/settings/notifications', label: 'Уведомления' },
  { href: '/dashboard/settings', label: 'Настройки' },
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
