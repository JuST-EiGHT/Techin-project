'use client'
// Главная (шаг 10.4, docs/SITEMAP.md п. 1) — «как идут дела у магазина?»: ключевые показатели с прошлым периодом, один
// график динамики, «Требует внимания», «Деньги и ближайшие события», «Рекомендуемые действия» (10.6). Решение владельца
// 22.10.2026: деньги из «Планирования» — на главной блоком, если планирование включено (было: только данные Ozon, 3.7).
// Виджеты «Заканчиваются» и «Последние изменения цен» ушли на свои места: остатки — «Склады» и «Требует внимания»
// (10.5), изменения цен — «Цены → История цен».
import { AlertTriangle, FileSpreadsheet, KeyRound } from 'lucide-react'
import Link from 'next/link'
import { Suspense, useEffect, useState } from 'react'

import { AttentionWidget } from '@/components/alerts'
import { AnalyticsFilters, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import type { OzonDashboard } from '@/components/dashboard'
import { DynamicsChart, KeyFigures, MoneyBlock } from '@/components/home'
import { Recommendations } from '@/components/recommendations'
import { IconBox, PageHeader, Panel, Skeleton, buttonClass } from '@/components/ui'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { useCostLabel } from '@/lib/settings'

export default function DashboardPage() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  )
}

/** Подключено ли что-нибудь, налог, баланс и выплаты Ozon — /dashboard/ozon (шаг 3.7) за тот же период. */
function useBoard(from: string, to: string) {
  const [board, setBoard] = useState<OzonDashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setError(null)
    api
      .get<OzonDashboard>('/dashboard/ozon', { params: { date_from: from, date_to: to } })
      .then(({ data }) => alive && setBoard(data))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [from, to])
  return { board, error }
}

function Dashboard() {
  const { user } = useAuth()
  const costLabel = useCostLabel()
  const [filter, update] = useAnalyticsFilter('month') // как раньше: по умолчанию — этот месяц
  const { data, error } = useMetrics(filter)
  const { board, error: boardError } = useBoard(filter.range.from, filter.range.to)
  const setup = board?.setup
  const nothing = setup && !setup.has_key && !setup.has_reports && !setup.has_balance

  const hasChart = !!data && data.coverage.data_source !== 'none'

  return (
    // дизайн-система 10.27a (docs/DESIGN.md, §5 «Обзор»): ответ — цифры, потом «почему» — график, рядом «что сделать»
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Как идут дела у магазина" description={`Добро пожаловать, ${user?.full_name || user?.email || ''}`}>
        {!nothing && (
          <AnalyticsFilters value={filter} onChange={update} showProduct={false} showCategory={false} showGranularity={false} kinds={['finance', 'postings', 'balance']} />
        )}
      </PageHeader>

      <div className="space-y-5">
        {setup && !setup.tax_set && (
          <div data-testid="tax-banner" className="flex flex-wrap items-center gap-3 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm">
            <IconBox icon={AlertTriangle} tone="warn" />
            <span className="min-w-0 flex-1 text-warn">
              Налог не задан: укажите, кто вы и какая у вас система налогообложения, — без этого сервис не посчитает налог в P&amp;L.
            </span>
            <Link href="/dashboard/settings/taxes" className={buttonClass('secondary', 'sm')}>
              Указать →
            </Link>
          </div>
        )}

        {nothing ? (
          <Connect />
        ) : (
          <>
            <MetricsState error={error ?? boardError} loading={false} empty={!data} />
            {!data && !error && !boardError && <HomeSkeleton />}
            {data && (
              <div className="space-y-2">
                <KeyFigures data={data} taxSet={setup?.tax_set ?? true} costLabel={costLabel} />
                <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
                  <div className="min-w-0 flex-1">
                    <CoverageNote data={data} />
                  </div>
                  <Link href="/dashboard/analytics/reports" className="text-xs text-accent hover:underline">
                    Отчёты Ozon →
                  </Link>
                </div>
              </div>
            )}
            {(hasChart || board) && (
              <div className="grid items-start gap-5 lg:grid-cols-3">
                {hasChart && data && (
                  <div className="min-w-0 lg:col-span-2">
                    <DynamicsChart data={data} granularity={{ value: filter.granularity, chosen: filter.granularityChosen, onChange: (g) => update({ granularity: g, granularityChosen: true }) }} />
                  </div>
                )}
                {/* «Требует внимания» (10.5): единый список проблем — аномалии и данные, по важности и деньгам под риском */}
                {board && (
                  <div className={`min-w-0 ${hasChart ? '' : 'lg:col-span-3'}`}>
                    <AttentionWidget />
                  </div>
                )}
              </div>
            )}
            {board && <MoneyBlock payouts={board.payouts} hasKey={board.setup.has_key} />}
            {/* «Рекомендуемые действия» (10.6): после основных блоков, только при достаточных данных */}
            {board && <Recommendations />}
          </>
        )}
      </div>
    </div>
  )
}

/** Пока грузится: заглушки той же формы — четыре показателя и график. */
function HomeSkeleton() {
  return (
    <div className="space-y-5" data-testid="home-loading">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl border border-line bg-card p-4 shadow-card">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="mt-3 h-7 w-3/4" />
            <Skeleton className="mt-2 h-4 w-2/3" />
          </div>
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="rounded-xl border border-line bg-card p-5 shadow-card lg:col-span-2">
          <Skeleton className="h-5 w-1/3" />
          <Skeleton className="mt-4 h-64" />
        </div>
        <div className="rounded-xl border border-line bg-card p-5 shadow-card">
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="mt-4 h-16" />
          <Skeleton className="mt-3 h-16" />
        </div>
      </div>
    </div>
  )
}

/** Ничего не подключено: что даёт ключ и что дают отчёты — пользователь выбирает одно или оба. */
function Connect() {
  return (
    <Panel
      testid="connect"
      title="Подключите данные Ozon"
      description="Главная показывает продажи, прибыль, заказы, баланс и выплаты Ozon. Данные можно взять двумя способами — можно один, можно оба: где есть отчёт, сервис берёт его, остальные дни — из API."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Link href="/dashboard/settings/integrations" className="group rounded-xl border border-line p-4 transition-colors hover:border-accent/50 hover:bg-soft/40" data-testid="connect-key">
          <IconBox icon={KeyRound} />
          <div className="mt-3 font-semibold">API-ключ Ozon →</div>
          <p className="mt-1 text-sm text-muted">
            Продажи, заказы, начисления, остатки, баланс и выплаты загружаются сами каждые несколько часов. Только так видны
            заказы, баланс у Ozon и даты выплат. Цифры по API могут немного расходиться с кабинетом.
          </p>
        </Link>
        <Link href="/dashboard/analytics/reports/upload" className="group rounded-xl border border-line p-4 transition-colors hover:border-accent/50 hover:bg-soft/40" data-testid="connect-reports">
          <IconBox icon={FileSpreadsheet} />
          <div className="mt-3 font-semibold">Отчёты XLSX из кабинета →</div>
          <p className="mt-1 text-sm text-muted">
            Отчёт по начислениям за неделю или месяц — P&amp;L точно как в личном кабинете Ozon. Загружаете вручную, когда
            Ozon сформирует отчёт.
          </p>
        </Link>
      </div>
    </Panel>
  )
}
