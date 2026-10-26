'use client'
// «Себестоимость» (шаг 10.15): полнота — товары с продажами без себестоимости и доля выручки без неё, список
// «заполнить»; сопоставление артикулов файла с товарами Ozon — привязка в строке загрузки и список привязок.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, fmtDay, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { productHref } from '@/lib/stocks'

export type CostCoverage = {
  date_from: string
  date_to: string
  data_source: string
  catalog_total: number
  catalog_with_cost: number
  sold_total: number
  sold_without_cost: number
  revenue_total: string | null
  revenue_without_cost: string | null
  revenue_share_without_cost: string | null
  items: { offer_id: string; name: string | null; product_id: string | null; qty: number; revenue: string; share: string | null }[]
}

const SHOWN = 5

/** Полнота себестоимости за 30 дней: без неё прибыль по этим товарам не считается (правило 5). */
export function CoverageBlock({ reloadKey, onFill }: { reloadKey: number; onFill: (offerId: string) => void }) {
  const [data, setData] = useState<CostCoverage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => {
    api
      .get<CostCoverage>('/costs/coverage')
      .then(({ data: d }) => {
        setData(d)
        setError(null)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [reloadKey])

  async function template() {
    if (!data) return
    const ids = data.items.map((i) => i.product_id).filter((id): id is string => id !== null)
    try {
      await saveFile(api.post('/costs/template/selected', { product_ids: ids, format: 'xlsx' }, { responseType: 'blob' }), 'costs.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать шаблон'))
    }
  }

  if (!data) return error ? <FormError message={error} /> : null
  const known = data.data_source !== 'none'
  const share = data.revenue_share_without_cost === null ? null : Number(data.revenue_share_without_cost)
  const catalogPct = data.catalog_total ? Math.round((data.catalog_with_cost / data.catalog_total) * 100) : 0
  const shown = all ? data.items : data.items.slice(0, SHOWN)
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="cost-coverage">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-semibold">Полнота себестоимости</h2>
        <span className="text-xs text-faint">
          продажи за {fmtDay(data.date_from)} – {fmtDay(data.date_to)}
        </span>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <Fact
          label="Товаров в продаже с себестоимостью"
          value={`${data.catalog_with_cost} из ${data.catalog_total}`}
          bar={catalogPct}
          tone={catalogPct === 100 ? 'bg-ok' : 'bg-accent'}
          testid="coverage-catalog"
        />
        <Fact
          label="Продавались без себестоимости"
          value={known ? `${data.sold_without_cost} из ${data.sold_total}` : 'нет данных'}
          tone={data.sold_without_cost ? 'bg-warn' : 'bg-ok'}
          bar={known && data.sold_total ? Math.round((data.sold_without_cost / data.sold_total) * 100) : null}
          testid="coverage-sold"
        />
        <Fact
          label="Выручка без себестоимости"
          value={known ? `${rub(data.revenue_without_cost)}${share !== null ? ` · ${pctText(data.revenue_share_without_cost)}` : ''}` : 'нет данных'}
          tone={share ? 'bg-warn' : 'bg-ok'}
          bar={share}
          testid="coverage-revenue"
        />
      </div>
      {!known ? (
        <p className="mt-3 text-sm text-muted">За 30 дней нет начислений — какие товары продавались, неизвестно (это не ноль). Обновите начисления или загрузите отчёт.</p>
      ) : data.items.length === 0 ? (
        <p className="mt-3 text-sm text-ok" data-testid="coverage-full">У всех товаров с продажами есть себестоимость — прибыль считается полностью.</p>
      ) : (
        <>
          <p className="mt-3 text-sm text-muted">
            По этим товарам прибыль не считается, поэтому прибыль магазина завышена. Заполните, начиная с тех, где больше выручки:
          </p>
          <ul className="mt-2 divide-y divide-line text-sm" data-testid="coverage-list">
            {shown.map((i) => (
              <li key={i.offer_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1.5" data-testid="coverage-item">
                <span className="min-w-0 flex-1">
                  {i.product_id ? (
                    <Link href={productHref(i.product_id)} className="font-medium hover:text-accent">
                      {i.offer_id}
                    </Link>
                  ) : (
                    <span className="font-medium">{i.offer_id}</span>
                  )}
                  {i.name && <span className="block truncate text-xs text-muted">{i.name}</span>}
                </span>
                <span className="tabular-nums text-muted">{i.qty} шт.</span>
                <span className="w-28 text-right tabular-nums">{rub(i.revenue)}</span>
                <span className="w-14 text-right text-xs tabular-nums text-faint">{pctText(i.share)}</span>
                <button type="button" onClick={() => onFill(i.offer_id)} className="text-accent hover:underline" data-testid="coverage-fill">
                  Указать
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {data.items.length > SHOWN && (
              <button type="button" onClick={() => setAll(!all)} className="text-muted hover:text-fg">
                {all ? 'Свернуть' : `Показать все (${data.sold_without_cost})`}
              </button>
            )}
            {data.items.some((i) => i.product_id) && (
              <button type="button" onClick={template} className="text-accent hover:underline" data-testid="coverage-template">
                Шаблон Excel с этими товарами
              </button>
            )}
          </div>
        </>
      )}
      <FormError message={error} />
    </section>
  )
}

function Fact({ label, value, bar, tone, testid }: { label: string; value: string; bar: number | null; tone: string; testid: string }) {
  return (
    <div className="rounded-lg bg-bg/60 px-3 py-2" data-testid={testid}>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 font-medium tabular-nums">{value}</div>
      {bar !== null && (
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
          <div className={`h-full ${tone}`} style={{ width: `${Math.min(Math.max(bar, 0), 100)}%` }} />
        </div>
      )}
    </div>
  )
}

/** Привязать артикул из файла к товару Ozon: похожие артикулы — на выбор. */
export function BindCell({ alias, suggestions, onBound }: { alias: string; suggestions: string[]; onBound: () => void }) {
  const [offer, setOffer] = useState(suggestions[0] ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function bind() {
    setBusy(true)
    setError(null)
    try {
      await api.post('/costs/aliases', { alias, offer_id: offer })
      onBound()
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs" data-testid="bind-cell">
      <Badge tone="warn" title="Такого артикула нет в каталоге Ozon этого магазина">
        нет в Ozon
      </Badge>
      {suggestions.length > 0 ? (
        <>
          <select value={offer} onChange={(e) => setOffer(e.target.value)} aria-label={`Товар Ozon для ${alias}`} className="rounded-md border border-line bg-bg px-1.5 py-0.5 text-xs outline-none focus:border-accent" data-testid="bind-select">
            {suggestions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button type="button" onClick={bind} disabled={busy || !offer} className="rounded-md border border-accent px-2 py-0.5 text-accent hover:bg-accent/10 disabled:opacity-60" data-testid="bind-button">
            {busy ? '…' : 'Привязать'}
          </button>
        </>
      ) : (
        <span className="text-faint">похожих в каталоге нет — загрузится как есть</span>
      )}
      {error && <span className="text-bad">{error}</span>}
    </span>
  )
}

type Alias = { id: string; alias: string; offer_id: string; name: string | null; created_at: string }

/** Запомненные привязки: по ним следующие файлы сопоставляются сами. */
export function AliasesBlock({ reloadKey, onChanged }: { reloadKey: number; onChanged: () => void }) {
  const [items, setItems] = useState<Alias[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api
      .get<Alias[]>('/costs/aliases')
      .then(({ data }) => setItems(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    load()
  }, [load, reloadKey])

  async function remove(a: Alias) {
    try {
      await api.delete(`/costs/aliases/${a.id}`)
      load()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!items || items.length === 0) return error ? <FormError message={error} /> : null
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="aliases">
      <h2 className="font-semibold">Сопоставленные артикулы</h2>
      <p className="mt-1 text-xs text-faint">Артикул в вашем файле → товар в Ozon. Следующие файлы сопоставятся сами; отвязать — если привязали не тот.</p>
      <ul className="mt-2 divide-y divide-line text-sm">
        {items.map((a) => (
          <li key={a.id} className="flex flex-wrap items-baseline gap-x-3 py-1.5" data-testid="alias-row">
            <span className="font-medium">{a.alias}</span>
            <span className="text-faint">→</span>
            <span>
              {a.offer_id}
              {a.name && <span className="text-xs text-muted"> · {a.name}</span>}
            </span>
            <button type="button" onClick={() => remove(a)} className="ml-auto text-xs text-muted hover:text-bad" data-testid="alias-remove">
              Отвязать
            </button>
          </li>
        ))}
      </ul>
      <FormError message={error} />
    </section>
  )
}
