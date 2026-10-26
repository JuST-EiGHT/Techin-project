'use client'
// «Аналитика → Аналитика продаж» (шаг 10.8, docs/SITEMAP.md п. 2.3): заказы, продажи, возвраты и средняя цена к прошлому
// периоду; график по дням / неделям / месяцам с событиями (цена, акция, нет остатка, снова в наличии); события периода;
// продажи по товарам — поиск, фильтры, сортировка, Excel. Фильтр — общий для подразделов «Аналитики» (10.7).
import { Suspense } from 'react'

import { AnalyticsFilters, AnalyticsTabs, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import { FormError } from '@/components/AuthForm'
import { EventsList, SalesChart, SalesFigures, SalesTable, useSalesData } from '@/components/sales-analytics'

export default function SalesAnalyticsPage() {
  return (
    <Suspense>
      <Sales />
    </Suspense>
  )
}

function Sales() {
  const [filter, update] = useAnalyticsFilter(undefined, { remember: true })
  const { data, error, loading } = useMetrics(filter)
  const { table, events, error: salesError } = useSalesData(filter)

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Аналитика</h1>
        <p className="mt-1 text-sm text-muted">
          Заказы — когда покупатель оформил, продажи — когда Ozon начислил выручку: за один период они не совпадают.
        </p>
      </div>
      <AnalyticsTabs />
      <AnalyticsFilters value={filter} onChange={update} kinds={['finance', 'postings', 'products', 'stocks', 'promotions']} />
      <MetricsState error={error} loading={loading} empty={!data} />
      {data && (
        <>
          <SalesFigures data={data} />
          <CoverageNote data={data} />
          {data.coverage.data_source !== 'none' && <SalesChart data={data} events={events} />}
        </>
      )}
      <FormError message={salesError} />
      {table && <EventsList events={events} />}
      {table && <SalesTable table={table} filter={filter} />}
    </div>
  )
}
