'use client'
// «Рекомендуемые действия» (шаг 10.6): что сделать, почему — условие и цифры, на каком расчёте основано, сколько на кону,
// «Скрыть» на N дней (настройка). «Сделано» не отмечается: условие ушло — рекомендация исчезает сама. На главной — после
// основных блоков; по одному товару (productId) — «Статистика товара» (10.14).
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { api, errorMessage } from '@/lib/api'

export type Recommendation = {
  kind: 'promo_loss' | 'price_below_min' | 'supply' | 'missing_cost' | 'withdraw'
  key: string
  title: string
  why: string
  basis: string
  action: string
  link: string
  product: { id: string; offer_id: string; name: string | null } | null
  money: string | null
  money_label: string | null
}

export type RecommendationsPage = {
  items: Recommendation[]
  total: number
  hidden: number
  data_days: number
  min_days: number
  enough_data: boolean
  hide_days: number
}

const rubles = (v: string) => `${Math.round(Number(v)).toLocaleString('ru-RU')} ₽`

export function Recommendations({ productId, limit = 5 }: { productId?: string; limit?: number }) {
  const [data, setData] = useState<RecommendationsPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  const load = useCallback(() => {
    setError(null)
    api
      .get<RecommendationsPage>('/recommendations', { params: productId ? { product_id: productId } : {} })
      .then(({ data }) => setData(data))
      .catch((err) => setError(errorMessage(err)))
  }, [productId])
  useEffect(() => {
    load()
  }, [load])

  async function hide(r: Recommendation) {
    try {
      await api.post('/recommendations/hide', { key: r.key })
      setData((d) => d && { ...d, items: d.items.filter((x) => x.key !== r.key), total: d.total - 1, hidden: d.hidden + 1 })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function unhide() {
    try {
      await api.post('/recommendations/unhide')
      load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!data) return error ? <FormError message={error} /> : null
  const shown = all ? data.items : data.items.slice(0, limit)
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="recommendations">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Рекомендуемые действия</h2>
        {data.total > 0 && <span className="text-sm text-muted">{data.total}</span>}
        {data.hidden > 0 && (
          <button type="button" onClick={unhide} className="ml-auto text-sm text-accent hover:underline" data-testid="rec-unhide">
            Скрыто {data.hidden} — вернуть
          </button>
        )}
      </div>
      <FormError message={error} />
      {!data.enough_data ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="rec-not-enough">
          Советы появятся, когда наберётся {data.min_days} дней продаж с данными за последние 30 (сейчас — {data.data_days}). Раньше
          они были бы «примерно», а рекомендуем мы только по точным цифрам.
        </p>
      ) : data.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="rec-empty">
          Советов нет: цены не ниже минимальных, акции не в минус, поставки не горят, себестоимость заполнена, лишнего на складах нет.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {shown.map((r) => (
            <li key={r.key} className="px-4 py-3" data-testid="rec-item" data-kind={r.kind}>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{r.title}</p>
                  {r.product?.name && <p className="truncate text-xs text-faint">{r.product.name}</p>}
                  <p className="mt-1 text-sm text-muted" data-testid="rec-why">
                    {r.why}
                  </p>
                  <p className="mt-1 text-xs text-faint" data-testid="rec-basis">
                    Расчёт: {r.basis}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                    <Link
                      href={r.link}
                      className="rounded-lg bg-accent px-3 py-1 font-medium text-white hover:opacity-90"
                      data-testid="rec-action"
                    >
                      {r.action} →
                    </Link>
                    {r.money !== null && (
                      <span className="text-warn" data-testid="rec-money">
                        ≈ {rubles(r.money)}
                        {r.money_label ? ` — ${r.money_label}` : ''}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => hide(r)}
                  title={`Скрыть на ${data.hide_days} дн. Если условие останется — совет вернётся.`}
                  className="shrink-0 rounded-lg border border-line px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent"
                  data-testid="rec-hide"
                >
                  Скрыть
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {!all && data.items.length > limit && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="block w-full border-t border-line px-4 py-2.5 text-left text-sm text-accent hover:underline"
          data-testid="rec-more"
        >
          Показать все ({data.items.length})
        </button>
      )}
    </section>
  )
}
