'use client'
// «Аналитика → ABC-анализ» (шаг 10.10, docs/SITEMAP.md п. 2.5): группы A, B, C по выручке, прибыли или продажам за период
// (границы — «Настройки»), сколько товаров и какой вклад, сравнение ABC по выручке и по прибыли, таблица с долей,
// накопленной долей, прошлым периодом и сменой группы, Excel. Фильтр — общий для подразделов «Аналитики» (10.7).
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'

import { AbcDistribution, AbcGroups, AbcMatrix, AbcTable, CRITERIA, type Criterion, useAbc } from '@/components/abc-analytics'
import { AnalyticsFilters, AnalyticsTabs, MetricsState, useAnalyticsFilter } from '@/components/analytics'

export default function AbcPage() {
  return (
    <Suspense>
      <Abc />
    </Suspense>
  )
}

function Abc() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const raw = params.get('by')
  const by: Criterion = CRITERIA.some((c) => c.value === raw) ? (raw as Criterion) : 'revenue'
  const [filter, update] = useAnalyticsFilter(undefined, { remember: true })
  const { data, error, loading } = useAbc(filter, by)
  const [pick, setPick] = useState('all')

  function setBy(next: Criterion) {
    const q = new URLSearchParams(params.toString())
    if (next === 'revenue') q.delete('by')
    else q.set('by', next)
    setPick('all')
    router.push(q.toString() ? `${pathname}?${q}` : pathname, { scroll: false })
  }
  const toggle = (p: string) => setPick((cur) => (cur === p ? 'all' : p))

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Аналитика</h1>
        <p className="mt-1 text-sm text-muted">
          Какие товары приносят основную часть выручки, прибыли или продаж — группы A, B и C — и как они менялись.
        </p>
      </div>
      <AnalyticsTabs />
      <AnalyticsFilters
        value={filter}
        onChange={(patch) => {
          setPick('all')
          update(patch)
        }}
        kinds={['finance', 'products']}
        showCompare={false}
        showGranularity={false}
      />
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Критерий">
        <span className="text-sm text-muted">Делить по:</span>
        {CRITERIA.map((c) => (
          <button
            key={c.value}
            type="button"
            onClick={() => setBy(c.value)}
            aria-pressed={by === c.value}
            className={`rounded-lg border px-3 py-1 text-sm ${by === c.value ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
            data-testid={`abc-by-${c.value}`}
          >
            {c.label}
          </button>
        ))}
      </div>
      <MetricsState error={error} loading={loading} empty={!data} />
      {data && data.items.length === 0 && (
        <p className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="abc-empty">
          {data.data_source === 'none'
            ? 'За период нет начислений Ozon — делить товары не из чего. Выберите другой период или загрузите отчёт по начислениям.'
            : 'За период нет продаж выбранных товаров.'}
        </p>
      )}
      {data && data.items.length > 0 && (
        <>
          {filter.product && (
            <p className="rounded-lg bg-accent/10 px-3 py-2 text-sm" data-testid="abc-one-product">
              Выбран один товар — он всегда в группе A. ABC сравнивает товары между собой: смотрите магазин целиком или категорию.
            </p>
          )}
          {(data.unknown > 0 || data.loss.count > 0) && (
            <p className="text-sm text-muted" data-testid="abc-notes">
              {data.loss.count > 0 && (
                <span className="text-bad">
                  Убыточных — {data.loss.count} ({formatLoss(data.loss.value)}): они в группе C, их доля не считается.{' '}
                </span>
              )}
              {data.unknown > 0 && (
                <span className="text-warn">Без себестоимости — {data.unknown}: прибыль неизвестна, группы по прибыли нет.</span>
              )}
            </p>
          )}
          <AbcGroups data={data} picked={pick} onPick={toggle} />
          <AbcDistribution data={data} onPick={toggle} />
          <AbcMatrix data={data} picked={pick} onPick={toggle} />
          <AbcTable data={data} filter={filter} pick={pick} onPick={setPick} />
        </>
      )}
    </div>
  )
}

const formatLoss = (v: string) => `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽`.replace('-', '−')
