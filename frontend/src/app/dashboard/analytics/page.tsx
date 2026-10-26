'use client'
// «Аналитика → Обзор бизнеса» (шаг 10.7, docs/SITEMAP.md п. 2.1–2.2): рамка раздела (вкладки подразделов, общие фильтры
// 10.3 со сравнением и «обновлено …») и обзор — выручка, прибыль, маржинальность и продажи в штуках к прошлому периоду,
// график, лучшие и худшие товары, переходы к подробным отчётам. Фильтр запоминается и переходит в соседние подразделы.
import { Suspense, useEffect, useState } from 'react'

import { LeadersBlock, ReportLinks } from '@/components/analytics-overview'
import { AnalyticsFilters, AnalyticsTabs, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import { DynamicsChart, KeyFigures } from '@/components/home'
import { api } from '@/lib/api'
import { useCostLabel } from '@/lib/settings'

export default function AnalyticsOverviewPage() {
  return (
    <Suspense>
      <Overview />
    </Suspense>
  )
}

function Overview() {
  const costLabel = useCostLabel()
  const [filter, update] = useAnalyticsFilter(undefined, { remember: true })
  const { data, error, loading } = useMetrics(filter)
  const [taxSet, setTaxSet] = useState(true)
  useEffect(() => {
    api
      .get<{ current: unknown | null }>('/tax-profile')
      .then(({ data }) => setTaxSet(data.current !== null))
      .catch(() => setTaxSet(true))
  }, [])

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Аналитика</h1>
        <p className="mt-1 text-sm text-muted">Как идут продажи и прибыль за период и что изменилось к прошлому такому же периоду.</p>
      </div>
      <AnalyticsTabs />
      <AnalyticsFilters value={filter} onChange={update} kinds={['finance', 'postings', 'products']} />
      <MetricsState error={error} loading={loading} empty={!data} />
      {data && (
        <>
          <KeyFigures data={data} taxSet={taxSet} costLabel={costLabel} fourth="sold_qty" />
          <CoverageNote data={data} />
          {data.coverage.data_source !== 'none' && <DynamicsChart data={data} />}
        </>
      )}
      {filter.product ? (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted" data-testid="leaders-one-product">
          Выбран один товар — лучшие и худшие товары видны, когда смотрите магазин целиком или категорию.
        </p>
      ) : (
        <LeadersBlock filter={filter} />
      )}
      <ReportLinks from={filter.range.from} to={filter.range.to} />
    </div>
  )
}
