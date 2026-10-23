'use client'
// Главная (шаг 10.4, docs/SITEMAP.md п. 1) — «как идут дела у магазина?»: ключевые показатели с прошлым периодом, один
// график динамики, «Требует внимания», «Деньги и ближайшие события», «Рекомендуемые действия». Решение владельца
// 22.10.2026: деньги из «Планирования» — на главной блоком, если планирование включено (было: только данные Ozon, 3.7).
// Виджеты «Заканчиваются» и «Последние изменения цен» ушли на свои места: остатки — «Склады» и «Требует внимания»
// (10.5), изменения цен — «Цены → История цен».
import Link from 'next/link'
import { Suspense, useEffect, useState } from 'react'

import { AttentionWidget } from '@/components/alerts'
import { AnalyticsFilters, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import type { OzonDashboard } from '@/components/dashboard'
import { DynamicsChart, KeyFigures, MoneyBlock } from '@/components/home'
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
  const { data, error, loading } = useMetrics(filter)
  const { board, error: boardError } = useBoard(filter.range.from, filter.range.to)
  const setup = board?.setup
  const nothing = setup && !setup.has_key && !setup.has_reports && !setup.has_balance

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <p className="text-sm text-muted">Добро пожаловать, {user?.full_name || user?.email}</p>
        <h1 className="text-2xl font-semibold">Как идут дела у магазина</h1>
      </div>

      {setup && !setup.tax_set && (
        <div
          data-testid="tax-banner"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn"
        >
          Налог не задан: укажите, кто вы и какая у вас система налогообложения, — без этого сервис не посчитает налог в P&amp;L.
          <Link href="/dashboard/settings/taxes" className="ml-auto underline">
            Указать →
          </Link>
        </div>
      )}

      {nothing ? (
        <Connect />
      ) : (
        <>
          <AnalyticsFilters value={filter} onChange={update} showProduct={false} showCategory={false} kinds={['finance', 'postings', 'balance']} />
          <MetricsState error={error ?? boardError} loading={loading} empty={!data} />
          {data && (
            <>
              <KeyFigures data={data} taxSet={setup?.tax_set ?? true} costLabel={costLabel} />
              <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
                <div className="min-w-0 flex-1">
                  <CoverageNote data={data} />
                </div>
                <Link href="/dashboard/analytics/reports" className="text-xs text-accent hover:underline">
                  Отчёты Ozon →
                </Link>
              </div>
              {data.coverage.data_source !== 'none' && <DynamicsChart data={data} />}
            </>
          )}
          {/* «Требует внимания» (10.5): единый список проблем — аномалии и данные, по важности и деньгам под риском */}
          {board && <AttentionWidget />}
          {board && <MoneyBlock payouts={board.payouts} hasKey={board.setup.has_key} />}
          {/* «Рекомендуемые действия» — шаг 10.6: после основных блоков, только при достаточных данных */}
        </>
      )}
      {!board && !boardError && !nothing && !data && <p className="text-sm text-muted">Загружаем…</p>}
    </div>
  )
}

/** Ничего не подключено: что даёт ключ и что дают отчёты — пользователь выбирает одно или оба. */
function Connect() {
  return (
    <section className="rounded-xl border border-line bg-card p-6" data-testid="connect">
      <h2 className="text-lg font-semibold">Подключите данные Ozon</h2>
      <p className="mt-1 text-sm text-muted">
        Главная показывает продажи, прибыль, заказы, баланс и выплаты Ozon. Данные можно взять двумя способами — можно один,
        можно оба: где есть отчёт, сервис берёт его, остальные дни — из API.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Link href="/dashboard/settings/integrations" className="rounded-lg border border-line p-4 hover:border-accent" data-testid="connect-key">
          <div className="font-semibold">API-ключ Ozon →</div>
          <p className="mt-1 text-sm text-muted">
            Продажи, заказы, начисления, остатки, баланс и выплаты загружаются сами каждые несколько часов. Только так видны
            заказы, баланс у Ozon и даты выплат. Цифры по API могут немного расходиться с кабинетом.
          </p>
        </Link>
        <Link href="/dashboard/analytics/reports/upload" className="rounded-lg border border-line p-4 hover:border-accent" data-testid="connect-reports">
          <div className="font-semibold">Отчёты XLSX из кабинета →</div>
          <p className="mt-1 text-sm text-muted">
            Отчёт по начислениям за неделю или месяц — P&amp;L точно как в личном кабинете Ozon. Загружаете вручную, когда
            Ozon сформирует отчёт.
          </p>
        </Link>
      </div>
    </section>
  )
}
