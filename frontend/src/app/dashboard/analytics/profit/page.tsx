'use client'
// «Аналитика → Аналитика прибыльности» (шаг 10.9, docs/SITEMAP.md п. 2.4): выручка, расходы, прибыль и маржа к прошлому
// периоду; график; структура расходов (статья → товары → исходные строки начислений); таблица «Прибыли по товарам» с
// быстрыми фильтрами — убыток, низкая маржа, нет себестоимости. Фильтр — общий для подразделов «Аналитики» (10.7).
import Link from 'next/link'
import { Suspense } from 'react'

import { AnalyticsFilters, AnalyticsTabs, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import { ProductsView } from '@/components/products-pnl'
import { ExpenseStructure, ProfitChart, ProfitFigures, profitParams, useProfitStructure } from '@/components/profit-analytics'
import { useCostLabel } from '@/lib/settings'

export default function ProfitAnalyticsPage() {
  return (
    <Suspense>
      <Profit />
    </Suspense>
  )
}

function Profit() {
  const costLabel = useCostLabel()
  const [filter, update] = useAnalyticsFilter(undefined, { remember: true })
  const { data, error, loading } = useMetrics(filter)
  const { data: s, error: sError } = useProfitStructure(filter)
  const query = profitParams(filter)
  const { from, to } = filter.range

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Аналитика</h1>
        <p className="mt-1 text-sm text-muted">
          Сколько заработали и на что ушли деньги: расходы по группам, товары в минус и с низкой маржой — до исходных начислений Ozon.
        </p>
      </div>
      <AnalyticsTabs />
      <AnalyticsFilters value={filter} onChange={update} kinds={['finance', 'products']} />
      <MetricsState error={error ?? sError} loading={loading} empty={!data && !s} />
      {s && <ProfitFigures s={s} costLabel={costLabel} />}
      {s && s.missing_cost > 0 && (
        <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm" data-testid="profit-missing-cost">
          {costLabel}: не указана у {s.missing_cost} тов. с продажами — их прибыль не считаем, расходы и прибыль неполные.{' '}
          <Link href="/dashboard/products/costs?missing=1" className="text-accent hover:underline">
            Указать →
          </Link>
        </p>
      )}
      {data && (
        <>
          <CoverageNote data={data} />
          {data.coverage.data_source !== 'none' && <ProfitChart data={data} />}
        </>
      )}
      {s && <ExpenseStructure key={JSON.stringify(query)} s={s} filter={filter} />}
      <section className="space-y-3" data-testid="profit-products">
        <h2 className="font-semibold">Прибыль по товарам</h2>
        <ProductsView
          key={JSON.stringify(query)}
          query={query}
          showSource={false}
          pnlHref={`/dashboard/finance/pnl?period=custom&from=${from}&to=${to}`}
          quick
          moneyWent={false}
        />
      </section>
    </div>
  )
}
