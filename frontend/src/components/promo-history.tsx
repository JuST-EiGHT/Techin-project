'use client'
// История участия в акциях (шаг 4.3): в каких акциях был товар, когда вошёл и вышел, по какой цене.
// 10.16: результат каждого участия — продажи и прибыль во время против такого же периода до.
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { ResultLine } from '@/components/promo-results'
import { Badge, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'
import { HISTORY_STATUS, fmtDate, type HistoryResponse } from '@/lib/promotions'

const PERIODS = [
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
  { days: 365, label: 'год' },
]

export function PromoHistory({ productId, onClearProduct }: { productId: string | null; onClearProduct: () => void }) {
  const [days, setDays] = useState(90)
  const [search, setSearch] = useState('')
  const [data, setData] = useState<HistoryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    const today = todayMsk()
    api
      .get<HistoryResponse>('/promotions/history', {
        params: { date_from: addDays(today, -(days - 1)), date_to: today, ...(productId ? { product_id: productId } : {}) },
      })
      .then((r) => {
        setData(r.data)
        setError(null)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [days, productId])

  const q = search.trim().toLowerCase()
  const items = (data?.items ?? []).filter(
    (i) => !q || [i.product.offer_id, i.product.name ?? '', i.promotion.title].some((t) => t.toLowerCase().includes(q)),
  )
  const product = productId && data?.items[0]?.product
  return (
    <section className="space-y-3" data-testid="promo-history">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Период">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              type="button"
              aria-pressed={days === p.days}
              onClick={() => setDays(p.days)}
              className={`rounded-md px-3 py-1 text-sm ${days === p.days ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {productId ? (
          <span className="rounded-full border border-accent px-3 py-1 text-sm text-accent">
            Товар {product ? product.offer_id : ''}{' '}
            <button type="button" onClick={onClearProduct} aria-label="Показать все товары" className="ml-1">
              ✕
            </button>
          </span>
        ) : (
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Товар или акция"
            aria-label="Поиск в истории"
            className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent sm:max-w-xs"
          />
        )}
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && items.length === 0 && (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">
          За этот период участий в акциях нет. История копится с первой синхронизации акций — прошлого Ozon не отдаёт.
        </p>
      )}
      {items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line bg-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[56rem] text-[13px]" data-testid="history-table">
              <thead className="border-b border-line text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-normal">Товар</th>
                  <th className="px-3 py-2 font-normal">Акция</th>
                  <th className="px-3 py-2 font-normal">Участие</th>
                  <th className="px-3 py-2 text-right font-normal">Цена в акции</th>
                  <th className="px-3 py-2 font-normal">Как попал</th>
                  <th className="px-3 py-2 font-normal">Результат</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => {
                  const st = HISTORY_STATUS[i.status]
                  const prices = i.prices.map((p) => p.action_price).filter((v, n, all) => v !== all[n - 1])
                  return (
                    <tr key={i.id} className="border-b border-line last:border-b-0" data-testid="history-row">
                      <td className="max-w-[14rem] px-3 py-2">
                        <div className="truncate font-medium">{i.product.offer_id}</div>
                        {i.product.name && <div className="truncate text-xs text-muted">{i.product.name}</div>}
                      </td>
                      <td className="max-w-[14rem] px-3 py-2">
                        <Link href={`/dashboard/products/promotions/${i.promotion.id}`} className="block truncate hover:text-accent">
                          {i.promotion.title}
                        </Link>
                        <div className="text-xs text-faint">
                          {fmtDate(i.promotion.date_from)} — {fmtDate(i.promotion.date_to)}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <div>
                          {fmtDate(i.entered_at)} — {i.left_at ? fmtDate(i.left_at) : 'сейчас'}
                        </div>
                        <Badge tone={st.tone}>{st.label}</Badge>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                        {prices.length > 1 ? (
                          <span title="Цена в акции менялась">
                            <span className="text-faint">{prices.slice(0, -1).map((v) => rub(v)).join(' → ')} → </span>
                            {rub(prices[prices.length - 1])}
                          </span>
                        ) : (
                          rub(i.action_price)
                        )}
                        {i.price && <div className="text-xs text-faint">обычная {rub(i.price)}</div>}
                      </td>
                      <td className="px-3 py-2">{i.added_automatically ? <Badge tone="warn">Ozon автоматически</Badge> : <span className="text-muted">вы</span>}</td>
                      <td className="min-w-[12rem] px-3 py-2">
                        <ResultLine r={i.result} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="text-xs text-faint">
        Даты — по синхронизациям акций (каждые 3 часа) и по вашим действиям в сервисе. История копится с первой синхронизации — прошлого
        Ozon не отдаёт. Результат — продажи и прибыль за дни участия против такого же числа дней до входа; дни без начислений не
        считаются.
      </p>
    </section>
  )
}
