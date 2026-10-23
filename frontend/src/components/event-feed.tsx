'use client'
// «История изменений и событий» (шаг 10.11): одна лента событий магазина — цены (через сервис и нет), акции, остатки и
// поставки, аномалии, действия пользователя, ошибки загрузки — по дням, новые сверху, «Показать ещё» по курсору.
// Данные — GET /analytics/events; лента собирается из уже хранимых данных (services/event_feed).
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import type { AnalyticsFilter } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'

export type FeedKind = 'price' | 'promo' | 'stock' | 'anomaly' | 'action' | 'error'
export type FeedEvent = {
  key: string
  at: string
  kind: FeedKind
  kind_title: string
  title: string
  detail: string | null
  tone: 'ok' | 'bad' | 'warn' | null
  product: { id: string | null; offer_id: string; name: string | null } | null
  link: string | null
  link_label: string | null
}
type FeedPage = { items: FeedEvent[]; next: string | null; kinds: { kind: FeedKind; title: string }[] }

export const KINDS: { kind: FeedKind; title: string }[] = [
  { kind: 'price', title: 'Цены' },
  { kind: 'promo', title: 'Акции' },
  { kind: 'stock', title: 'Остатки и поставки' },
  { kind: 'anomaly', title: 'Аномалии' },
  { kind: 'action', title: 'Действия' },
  { kind: 'error', title: 'Ошибки загрузки' },
]
const MARK: Record<FeedKind, string> = { price: '₽', promo: '%', stock: '▦', anomaly: '!', action: '✎', error: '×' }
const TONE: Record<string, string> = { ok: 'bg-ok/15 text-ok', bad: 'bg-bad/15 text-bad', warn: 'bg-warn/15 text-warn' }
const TZ = 'Europe/Moscow'
const dayKey = (iso: string) => new Date(iso).toLocaleDateString('sv-SE', { timeZone: TZ })
const dayTitle = (iso: string) =>
  new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', weekday: 'short', timeZone: TZ })
const timeOf = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: TZ })

function params(f: AnalyticsFilter, kinds: FeedKind[]) {
  return {
    date_from: f.range.from,
    date_to: f.range.to,
    ...(f.product ? { product_id: f.product } : {}),
    ...(f.category !== null ? { category_id: String(f.category) } : {}),
    ...(kinds.length ? { kind: kinds } : {}),
  }
}

/** Лента по фильтру: первая страница при смене фильтра, «ещё» — следующая по курсору. */
export function useFeed(filter: AnalyticsFilter, kinds: FeedKind[]) {
  const [items, setItems] = useState<FeedEvent[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)
  const key = JSON.stringify(params(filter, kinds))

  const load = useCallback(
    async (before: string | null) => {
      const id = ++seq.current
      setLoading(true)
      setError(null)
      try {
        const { data } = await api.get<FeedPage>('/analytics/events', {
          params: { ...JSON.parse(key), ...(before ? { before } : {}) },
          paramsSerializer: { indexes: null },
        })
        if (id !== seq.current) return
        setItems((cur) => (before ? [...cur, ...data.items] : data.items))
        setNext(data.next)
      } catch (err) {
        if (id === seq.current) setError(errorMessage(err))
      } finally {
        if (id === seq.current) setLoading(false)
      }
    },
    [key],
  )
  useEffect(() => {
    load(null)
  }, [load])
  return { items, next, loading, error, more: () => next && load(next) }
}

/** Выбор видов событий: «Все» или несколько. */
export function KindFilter({ value, onChange }: { value: FeedKind[]; onChange: (v: FeedKind[]) => void }) {
  const chip = (active: boolean) =>
    `rounded-lg border px-2.5 py-1 text-sm ${active ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Виды событий" data-testid="feed-kinds">
      <button type="button" onClick={() => onChange([])} aria-pressed={value.length === 0} className={chip(value.length === 0)} data-testid="feed-kind-all">
        Все
      </button>
      {KINDS.map((k) => {
        const on = value.includes(k.kind)
        return (
          <button
            key={k.kind}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== k.kind) : [...value, k.kind])}
            className={chip(on)}
            data-testid={`feed-kind-${k.kind}`}
          >
            <span className="mr-1 text-faint">{MARK[k.kind]}</span>
            {k.title}
          </button>
        )
      })}
    </div>
  )
}

/** Лента по дням: время, вид, что случилось, товар и куда перейти. */
export function FeedList({ items, next, loading, error, onMore }: { items: FeedEvent[]; next: string | null; loading: boolean; error: string | null; onMore: () => void }) {
  if (error && !items.length) return <FormError message={error} />
  if (loading && !items.length) return <p className="text-sm text-muted">Загрузка…</p>
  if (!items.length)
    return (
      <p className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="feed-empty">
        За этот период событий нет. Выберите период длиннее или другие виды событий.
      </p>
    )
  const days: { key: string; at: string; items: FeedEvent[] }[] = []
  for (const e of items) {
    const k = dayKey(e.at)
    if (days.at(-1)?.key !== k) days.push({ key: k, at: e.at, items: [] })
    days.at(-1)!.items.push(e)
  }
  return (
    <div className="space-y-4" data-testid="feed">
      {days.map((d) => (
        <section key={d.key} className="rounded-xl border border-line bg-card" data-testid="feed-day">
          <h2 className="border-b border-line px-4 py-2 text-sm font-semibold first-letter:uppercase">{dayTitle(d.at)}</h2>
          <ul className="divide-y divide-line">
            {d.items.map((e) => (
              <li key={e.key} className="flex gap-3 px-4 py-2.5 text-sm" data-testid="feed-event" data-kind={e.kind}>
                <span className="w-11 shrink-0 pt-0.5 tabular-nums text-muted">{timeOf(e.at)}</span>
                <span
                  className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-semibold ${e.tone ? TONE[e.tone] : 'bg-line text-muted'}`}
                  title={e.kind_title}
                  aria-label={e.kind_title}
                >
                  {MARK[e.kind]}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="break-words font-medium">{e.title}</div>
                  {e.detail && <div className="break-words text-xs text-muted">{e.detail}</div>}
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs">
                    <span className="text-faint">{e.kind_title}</span>
                    {e.product?.id && (
                      <Link href={`/dashboard/products?product=${e.product.id}`} className="text-muted hover:text-accent" data-testid="feed-product">
                        {e.product.offer_id}
                        {e.product.name ? ` · ${e.product.name}` : ''}
                      </Link>
                    )}
                    {e.link && (
                      <Link href={e.link} className="text-accent hover:underline" data-testid="feed-link">
                        {e.link_label ?? 'Подробнее'} →
                      </Link>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <FormError message={error} />
      {next && (
        <button
          type="button"
          onClick={onMore}
          disabled={loading}
          className="w-full rounded-xl border border-line bg-card py-2 text-sm text-accent hover:border-accent disabled:opacity-60"
          data-testid="feed-more"
        >
          {loading ? 'Загрузка…' : 'Показать ещё'}
        </button>
      )}
      {!next && <p className="text-center text-xs text-faint" data-testid="feed-end">Это все события за период</p>}
    </div>
  )
}
