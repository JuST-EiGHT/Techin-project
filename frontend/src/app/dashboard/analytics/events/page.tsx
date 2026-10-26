'use client'
// «Аналитика → История изменений и событий» (шаг 10.11, docs/SITEMAP.md п. 2.6): что происходило с магазином — цены,
// акции, остатки и поставки, аномалии, действия, ошибки загрузки — одной лентой. Фильтры: период, товар или категория
// (общий фильтр «Аналитики»), виды событий (в адресе: ?kind=price,promo).
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

import { AnalyticsFilters, AnalyticsTabs, useAnalyticsFilter } from '@/components/analytics'
import { FeedList, type FeedKind, KINDS, KindFilter, useFeed } from '@/components/event-feed'

export default function EventsPage() {
  return (
    <Suspense>
      <Events />
    </Suspense>
  )
}

function Events() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const kinds = (params.get('kind') ?? '').split(',').filter((k): k is FeedKind => KINDS.some((x) => x.kind === k))
  const [filter, update] = useAnalyticsFilter(undefined, { remember: true })
  const feed = useFeed(filter, kinds)

  function setKinds(next: FeedKind[]) {
    const q = new URLSearchParams(params.toString())
    if (next.length) q.set('kind', next.join(','))
    else q.delete('kind')
    router.push(q.toString() ? `${pathname}?${q}` : pathname, { scroll: false })
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Аналитика</h1>
        <p className="mt-1 text-sm text-muted">
          Что происходило с магазином: цены, акции, остатки, аномалии, ваши действия и ошибки загрузки — одной лентой, новые сверху.
        </p>
      </div>
      <AnalyticsTabs />
      <AnalyticsFilters value={filter} onChange={update} kinds={['products', 'stocks', 'promotions', 'finance']} showCompare={false} showGranularity={false} />
      <KindFilter value={kinds} onChange={setKinds} />
      <FeedList items={feed.items} next={feed.next} loading={feed.loading} error={feed.error} onMore={() => feed.more()} />
    </div>
  )
}
