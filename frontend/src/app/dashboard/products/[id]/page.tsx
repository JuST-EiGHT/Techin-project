'use client'
// «Статистика товара» (шаг 10.14, docs/SITEMAP.md п. 3.2): отдельная страница товара вместо боковой панели — показатели
// и динамика за период (фильтр в адресе, как в «Аналитике»), юнит-экономика, цены и скидки, история цен и акций, остатки
// по кластерам и поставки, себестоимость с правкой, расчёт цены, рекомендации; быстрые переходы. Сюда ведут «Все
// товары», уведомления, «Требует внимания», таблицы аналитики и склады; старый адрес ?product= переадресуется сюда.
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { AnalyticsFilters, CoverageNote, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import { FormError } from '@/components/AuthForm'
import { DynamicsChart, KeyFigures } from '@/components/home'
import { PriceCalcBlock } from '@/components/price-calc'
import { PriceChangeModal } from '@/components/price-change'
import { CostBlock, IdLine, PriceChart } from '@/components/product-panel'
import { Box, CostHistory, PriceChangesList, PricesBlock, ProblemList, PromosBlock, StocksBlock, UnitBlock } from '@/components/product-page'
import { Recommendations } from '@/components/recommendations'
import { Badge } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { type ProductStats } from '@/lib/products'
import { useCostLabel, useLowMargin } from '@/lib/settings'

export default function ProductRoute() {
  return (
    <Suspense>
      <ProductPage />
    </Suspense>
  )
}

function ProductPage() {
  const { id } = useParams<{ id: string }>()
  const [filter, update] = useAnalyticsFilter()
  const metrics = useMetrics({ ...filter, product: id, category: null })
  const [data, setData] = useState<ProductStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [reload, setReload] = useState(0)
  const [changing, setChanging] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const costLabel = useCostLabel()
  const lowMargin = useLowMargin()
  const { from, to } = filter.range

  useEffect(() => {
    let alive = true
    api
      .get<ProductStats>(`/products/${id}/stats`, { params: { date_from: from, date_to: to } })
      .then(({ data: d }) => {
        if (!alive) return
        setData(d)
        setError(null)
      })
      .catch((err) => {
        if (!alive) return
        if ((err as { response?: { status?: number } }).response?.status === 404) setNotFound(true)
        else setError(errorMessage(err))
      })
    return () => {
      alive = false
    }
  }, [id, from, to, reload])

  const refresh = useCallback(() => setReload((n) => n + 1), [])

  if (notFound) {
    return (
      <div className="mx-auto max-w-3xl space-y-4" data-testid="product-not-found">
        <BackLink />
        <section className="rounded-xl border border-line bg-card p-6">
          <h1 className="text-lg font-semibold">Товар не найден</h1>
          <p className="mt-2 text-sm text-muted">Его нет в этом магазине: возможно, ссылка от другого магазина или товар удалён из каталога Ozon.</p>
        </section>
      </div>
    )
  }

  const item = data?.detail.item
  const range = `period=custom&from=${from}&to=${to}&product=${id}`
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <BackLink />
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && item && (
        <>
          <header className="space-y-3" data-testid="product-head">
            <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
              <div className="min-w-0">
                <h1 className="break-words text-2xl font-semibold">{item.offer_id}</h1>
                <p className="text-sm text-muted">
                  {item.name ?? 'Без названия'}
                  {item.is_archived && (
                    <>
                      {' '}
                      <Badge tone="muted">в архиве Ozon</Badge>
                    </>
                  )}
                </p>
              </div>
              {!item.is_archived && (
                <button
                  type="button"
                  onClick={() => setChanging(true)}
                  className="ml-auto rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                  data-testid="product-change-price"
                >
                  Изменить цену
                </button>
              )}
            </div>
            <IdLine data={data.detail} />
            <ProblemList flags={item.flags} />
            <nav className="flex flex-wrap gap-2 text-sm" aria-label="Переходы по товару" data-testid="product-links">
              <Quick href="#calc">Расчёт цены</Quick>
              <Quick href={`/dashboard/products/promotions?tab=history&product=${id}`}>Акции товара</Quick>
              <Quick href="/dashboard/warehouses/supply">Поставка</Quick>
              <Quick href="#cost">Себестоимость</Quick>
              <Quick href={`/dashboard/analytics/sales?${range}`}>Аналитика продаж</Quick>
              <Quick href={`/dashboard/analytics/profit?${range}`}>Прибыльность</Quick>
              <Quick href={`/dashboard/analytics/events?${range}`}>История событий</Quick>
              <Quick href={`/dashboard/products/prices/history?product=${id}`}>История цен</Quick>
            </nav>
            {notice && (
              <div className="flex items-start gap-3 rounded-lg bg-ok/10 px-3 py-2 text-sm" role="status" data-testid="price-notice">
                <span>{notice}</span>
                <button type="button" onClick={() => setNotice(null)} className="ml-auto text-muted hover:text-fg" aria-label="Скрыть">
                  ✕
                </button>
              </div>
            )}
          </header>

          <AnalyticsFilters value={filter} onChange={update} showProduct={false} showCategory={false} kinds={['finance', 'postings', 'products']} />
          <MetricsState error={metrics.error} loading={metrics.loading} empty={!metrics.data} />
          {metrics.data && (
            <>
              <KeyFigures data={metrics.data} taxSet costLabel={costLabel} fourth="sold_qty" query={`product=${id}`} />
              <CoverageNote data={metrics.data} />
              {metrics.data.coverage.data_source !== 'none' && <DynamicsChart data={metrics.data} query={`product=${id}`} />}
            </>
          )}

          <Recommendations productId={id} />

          <div className="grid gap-5 lg:grid-cols-2">
            <UnitBlock data={data} costLabel={costLabel} lowMargin={lowMargin} profitHref={`/dashboard/analytics/profit?${range}`} />
            <PricesBlock data={data} lowMargin={lowMargin} onChange={item.is_archived ? null : () => setChanging(true)} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Box title="Цена за 30 дней" testid="product-price-history">
              <PriceChart data={data.detail} />
              <PriceChangesList data={data} />
            </Box>
            <PromosBlock data={data} lowMargin={lowMargin} />
          </div>

          <StocksBlock data={data} />

          <div className="grid gap-5 lg:grid-cols-2">
            {/* у блоков панели товара свои заголовки — карточки без второго */}
            <section id="cost" className="min-w-0 scroll-mt-20 rounded-xl border border-line bg-card p-4" data-testid="product-cost">
              <CostBlock key={data.cost_history.length} data={data.detail} onSaved={refresh} />
              <CostHistory data={data} />
            </section>
            <section id="calc" className="min-w-0 scroll-mt-20 rounded-xl border border-line bg-card p-4" data-testid="product-calc">
              <PriceCalcBlock productId={id} refreshKey={data} />
            </section>
          </div>
        </>
      )}
      {changing && item && (
        <PriceChangeModal
          product={{ id: item.id, offer_id: item.offer_id, name: item.name }}
          current={item.price}
          onClose={() => setChanging(false)}
          onDone={(message) => {
            setChanging(false)
            setNotice(message)
            refresh()
          }}
        />
      )}
    </div>
  )
}

function BackLink() {
  return (
    <Link href="/dashboard/products" className="inline-block text-sm text-muted hover:text-accent" data-testid="product-back">
      ← Все товары
    </Link>
  )
}

function Quick({ href, children }: { href: string; children: React.ReactNode }) {
  const cls = 'rounded-lg border border-line bg-card px-3 py-1.5 hover:border-accent hover:text-accent'
  if (href.startsWith('#')) {
    return (
      <a href={href} className={cls}>
        {children}
      </a>
    )
  }
  return (
    <Link href={href} className={cls}>
      {children}
    </Link>
  )
}
