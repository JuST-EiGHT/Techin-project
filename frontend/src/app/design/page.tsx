'use client'

// Витрина дизайн-системы (шаг 10.27a): все общие элементы на демо-данных в текущей теме. Правила — docs/DESIGN.md.
// Служебная страница: не в меню, без входа, данных магазина не читает. Разделы сверяются с ней при переделке.
import { AlertTriangle, Inbox, Moon, Package, ShoppingCart, Sun, TrendingUp, Wallet } from 'lucide-react'
import { useState } from 'react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { TipBox } from '@/components/charts'
import { ChipOptions, FilterButton, FilterField, SearchBox } from '@/components/filters'
import { useTokenColors } from '@/components/finance'
import { Badge, Button, Delta, EmptyState, IconBox, PageHeader, Panel, Segmented, Skeleton, Stat, StatGrid, buttonClass } from '@/components/ui'
import { useTheme } from '@/contexts/ThemeContext'

const rub = (v: number) => `${Math.round(v).toLocaleString('ru-RU')} ₽`.replace('-', '−')

// демо-данные: правдоподобные цифры магазина, не данные владельца
const DAYS = Array.from({ length: 30 }, (_, i) => {
  const d = i + 1
  const base = 182_000 + Math.sin(i / 3.2) * 38_000 + i * 2_100
  return { label: `${String(d).padStart(2, '0')}.10`, revenue: Math.round(base), profit: Math.round(base * (0.16 + Math.cos(i / 4) * 0.05)) }
})
const MONTHS = ['Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт'].map((m, i) => ({ label: m, now: [3.9, 4.4, 4.1, 5.2, 5.6, 6.1][i], prev: [3.1, 3.5, 3.8, 3.6, 4.2, 4.8][i] }))
const COSTS = [
  { name: 'Себестоимость', value: 2_410_000 },
  { name: 'Комиссия Ozon', value: 1_120_000 },
  { name: 'Логистика', value: 640_000 },
  { name: 'Реклама', value: 310_000 },
  { name: 'Хранение', value: 120_000 },
  { name: 'Прочее', value: 90_000 },
]
const ROWS = [
  { sku: '6205-2RS NACHI', name: 'Подшипник 6205-2RS', sold: 412, revenue: 287_340, margin: 24.1, status: 'ok' as const },
  { sku: '6304ZZE NACHI', name: 'Подшипник 6304ZZE', sold: 268, revenue: 203_150, margin: 18.7, status: 'ok' as const },
  { sku: '6000-2NSE', name: 'Подшипник 6000-2NSE', sold: 151, revenue: 61_420, margin: 6.2, status: 'warn' as const },
  { sku: '32210 NACHI', name: 'Подшипник роликовый конический 32210', sold: 37, revenue: 98_790, margin: -3.4, status: 'bad' as const },
]
const STATUS = { ok: { tone: 'ok', text: 'В наличии' }, warn: { tone: 'warn', text: 'Заканчивается' }, bad: { tone: 'bad', text: 'Убыток' } } as const

const TOKENS = ['bg', 'card', 'soft', 'line', 'fg', 'muted', 'faint', 'accent', 'ok', 'bad', 'warn'] as const
const SWATCH: Record<(typeof TOKENS)[number], string> = {
  bg: 'bg-bg', card: 'bg-card', soft: 'bg-soft', line: 'bg-line', fg: 'bg-fg', muted: 'bg-muted', faint: 'bg-faint',
  accent: 'bg-accent', ok: 'bg-ok', bad: 'bg-bad', warn: 'bg-warn',
}
const CHART_SWATCH = ['bg-chart-1', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5', 'bg-chart-6']

function Tip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <TipBox href={null} touch={false}>
      <div className="mb-1 font-medium">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center justify-between gap-4 tabular-nums">
          <span className="flex items-center gap-1.5 text-muted"><span className="h-2 w-2 rounded-full" style={{ background: p.color }} />{p.name}</span>
          {rub(p.value)}
        </div>
      ))}
    </TipBox>
  )
}

export default function DesignSystemPage() {
  const { theme, setPreference } = useTheme()
  const c = useTokenColors()
  const [metric, setMetric] = useState<'revenue' | 'profit'>('revenue')
  const [detail, setDetail] = useState<'day' | 'week' | 'month'>('day')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<'all' | 'ok' | 'warn' | 'bad'>('all')
  const total = COSTS.reduce((s, x) => s + x.value, 0)

  return (
    <main className="mx-auto max-w-[1280px] p-4 md:p-6">
      <PageHeader
        title="Дизайн-система"
        description="Общие элементы интерфейса на демо-данных. Правила — docs/DESIGN.md, элементы — components/ui.tsx."
        actions={
          <Button onClick={() => setPreference(theme === 'dark' ? 'light' : 'dark')} data-testid="theme-toggle">
            {theme === 'dark' ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
            {theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
          </Button>
        }
      />

      <div className="space-y-5">
        {/* цвета */}
        <Panel title="Цвета" description="Только токены темы. Свой цвет в компоненте — нельзя; новый — токеном в обеих темах.">
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6 lg:grid-cols-11">
            {TOKENS.map((t) => (
              <div key={t} className="text-xs">
                <div className={`h-12 rounded-lg border border-line ${SWATCH[t]}`} />
                <div className="mt-1.5 font-medium">{t}</div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted">
            Ряды графиков:
            {CHART_SWATCH.map((s, i) => (
              <span key={s} className="inline-flex items-center gap-1"><span className={`h-3 w-3 rounded-full ${s}`} />chart-{i + 1}</span>
            ))}
          </div>
        </Panel>

        {/* показатели */}
        <StatGrid testid="ds-stats">
          <Stat label="Выручка" value="6 128 400 ₽" icon={Wallet} delta={<Delta pct="12.4" />} hint="было 5 452 100 ₽" href="#" testid="ds-stat-revenue" />
          <Stat label="Прибыль от продаж" value="1 041 830 ₽" icon={TrendingUp} delta={<Delta pct="-3.1" />} hint="было 1 075 200 ₽" href="#" />
          <Stat label="Расходы на рекламу" value="310 000 ₽" icon={ShoppingCart} delta={<Delta pct="8.0" upIsBad />} hint="рост — плохо" />
          <Stat label="Маржинальность" value="17,0 %" icon={Package} delta={<Delta pct="-1.6" unit="п.п." />} hint="нет данных за 2 дня" />
        </StatGrid>

        {/* графики */}
        <div className="grid gap-5 lg:grid-cols-3">
          <Panel
            className="lg:col-span-2"
            title="Выручка по дням"
            description="01.10 – 30.10.2026 · наведите на точку — цифры, клик — день"
            action={<a href="#" className="text-accent hover:underline">Подробнее →</a>}
          >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <Segmented label="Показатель" value={metric} onChange={setMetric} options={[{ value: 'revenue', label: 'Выручка' }, { value: 'profit', label: 'Прибыль' }]} />
              <Segmented size="sm" label="Детализация" value={detail} onChange={setDetail} options={[{ value: 'day', label: 'Дни' }, { value: 'week', label: 'Недели' }, { value: 'month', label: 'Месяцы' }]} />
            </div>
            {c ? (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={DAYS} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="ds-area" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={c.accent} stopOpacity={0.28} />
                      <stop offset="100%" stopColor={c.accent} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke={c.line} />
                  <XAxis dataKey="label" tick={{ fill: c.faint, fontSize: 11 }} tickLine={false} axisLine={false} interval={4} />
                  <YAxis tick={{ fill: c.faint, fontSize: 11 }} tickLine={false} axisLine={false} width={44} tickFormatter={(v) => `${Math.round(v / 1000)}к`} />
                  <Tooltip content={<Tip />} cursor={{ stroke: c.accent, strokeOpacity: 0.4 }} />
                  <Area type="monotone" dataKey={metric} name={metric === 'revenue' ? 'Выручка' : 'Прибыль'} stroke={c.accent} strokeWidth={2.5} fill="url(#ds-area)" isAnimationActive={false} activeDot={{ r: 5, strokeWidth: 2, stroke: c.card }} />
                </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <Skeleton className="h-64" />
            )}
          </Panel>

          <Panel title="На что ушла выручка" description="Октябрь 2026">
            {c && (
              <div className="flex flex-col items-center">
                <PieChart width={180} height={180}>
                  <Pie data={COSTS} dataKey="value" nameKey="name" innerRadius={58} outerRadius={84} paddingAngle={2} stroke="none" isAnimationActive={false}>
                    {COSTS.map((x, i) => <Cell key={x.name} fill={[c.c1, c.c2, c.c3, c.c4, c.c5, c.c6][i]} />)}
                  </Pie>
                </PieChart>
                <ul className="mt-3 w-full space-y-1.5 text-sm">
                  {COSTS.map((x, i) => (
                    <li key={x.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-muted"><span className={`h-2.5 w-2.5 rounded-full ${CHART_SWATCH[i]}`} />{x.name}</span>
                      <span className="tabular-nums">{Math.round((x.value / total) * 100)}&nbsp;%</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Выручка по месяцам" description="Этот год и прошлый, млн ₽">
          {c && (
            <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
            <BarChart data={MONTHS} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={3} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke={c.line} />
              <XAxis dataKey="label" tick={{ fill: c.faint, fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: c.faint, fontSize: 11 }} tickLine={false} axisLine={false} width={32} />
              <Tooltip content={<Tip />} cursor={{ fill: c.soft }} />
              <Bar dataKey="prev" name="Прошлый год" fill={c.c6} radius={[6, 6, 0, 0]} maxBarSize={22} isAnimationActive={false} />
              <Bar dataKey="now" name="Этот год" fill={c.c1} radius={[6, 6, 0, 0]} maxBarSize={22} isAnimationActive={false} />
            </BarChart>
            </ResponsiveContainer>
            </div>
          )}
        </Panel>

        {/* таблица */}
        <Panel
          flush
          title="Товары"
          description="Таблица в панели во всю ширину: поиск и период снаружи, остальное — за «Фильтрами»"
          action={<button type="button" className={buttonClass('secondary', 'sm')}>Excel</button>}
        >
          <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
            <SearchBox value={q} onChange={setQ} className="w-full sm:w-80" />
            <div className="ml-auto">
              <FilterButton count={status === 'all' ? 0 : 1} onReset={() => setStatus('all')}>
                <FilterField label="Статус">
                  <ChipOptions value={status} onChange={setStatus} options={[{ value: 'all', label: 'Все' }, { value: 'ok', label: 'В наличии' }, { value: 'warn', label: 'Заканчивается' }, { value: 'bad', label: 'Убыток' }]} />
                </FilterField>
              </FilterButton>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-soft/60 text-left text-xs text-muted">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Товар</th>
                  <th className="px-3 py-2.5 text-right font-medium">Продано</th>
                  <th className="px-3 py-2.5 text-right font-medium">Выручка</th>
                  <th className="px-3 py-2.5 text-right font-medium">Маржа</th>
                  <th className="px-5 py-2.5 font-medium">Статус</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.filter((r) => (!q || `${r.sku} ${r.name}`.toLowerCase().includes(q.toLowerCase())) && (status === 'all' || status === r.status)).map((r) => (
                  <tr key={r.sku}>
                    <td className="px-5 py-3">
                      <div className="font-medium">{r.name}</div>
                      <div className="text-xs text-faint">{r.sku}</div>
                    </td>
                    <td className="px-3 py-3 text-right">{r.sold.toLocaleString('ru-RU')}&nbsp;шт.</td>
                    <td className="px-3 py-3 text-right">{rub(r.revenue)}</td>
                    <td className={`px-3 py-3 text-right ${r.margin < 0 ? 'text-bad' : ''}`}>{r.margin.toLocaleString('ru-RU')}&nbsp;%</td>
                    <td className="px-5 py-3"><Badge tone={STATUS[r.status].tone} dot>{STATUS[r.status].text}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* кнопки, плашки, состояния */}
        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title="Кнопки и плашки">
            <div className="flex flex-wrap gap-2">
              <Button variant="primary">Применить цены</Button>
              <Button>Excel</Button>
              <Button variant="ghost">Отмена</Button>
              <Button variant="danger">Отключить магазин</Button>
              <Button variant="primary" disabled>Сохранить</Button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Badge tone="accent">Ozon API</Badge>
              <Badge tone="ok" dot>Оплачен</Badge>
              <Badge tone="warn" dot>Ожидается</Badge>
              <Badge tone="bad" dot>Просрочен</Badge>
              <Badge tone="bad">СРОЧНО</Badge>
              <Badge tone="muted">Архив</Badge>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
              <Delta pct="12.4" /> <Delta pct="-3.1" /> <Delta pct="0" /> <Delta pct="8" upIsBad />
            </div>
          </Panel>

          <Panel title="Состояния" description="Нет данных ≠ 0: говорим, чего нет и откуда взять">
            <EmptyState icon={Inbox} title="Нет начислений за период" action={<Button variant="primary" size="sm">Загрузить из Ozon</Button>}>
              Ozon ещё не прислал данные за 01.11 – 11.11.2026. Обновите синхронизацию или загрузите отчёт файлом.
            </EmptyState>
            <div className="mt-2 flex items-start gap-3 rounded-lg bg-warn/10 p-3 text-sm">
              <IconBox icon={AlertTriangle} tone="warn" />
              <div>
                <div className="font-medium">Себестоимость не заполнена у 12 товаров</div>
                <div className="text-muted">Это 8 % выручки — прибыль по ним не считается.</div>
              </div>
            </div>
            <div className="mt-4 space-y-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          </Panel>
        </div>

        <Panel title="Текст" description="Inter с кириллицей; шкала — 12 / 14 / 16 / 24 / 30 px">
          <div className="space-y-2">
            <div className="text-2xl font-semibold tracking-tight">Заголовок страницы — 24 px</div>
            <div className="text-base font-semibold">Заголовок панели — 16 px</div>
            <div className="text-sm">Основной текст интерфейса — 14 px. Числа в таблицах — ровными колонками: 1 234 567,89 ₽</div>
            <div className="text-sm text-muted">Второстепенный текст — muted</div>
            <div className="text-xs text-faint">Подписи и подсказки — faint, 12 px</div>
          </div>
        </Panel>
      </div>
    </main>
  )
}
