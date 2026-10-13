'use client'
// Вкладка «По товарам» на странице P&L (шаг 3.4): прибыль и маржа каждого товара за период или по отчёту.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type Coverage, type DataSource, SourceNote } from '@/components/finance'
import { Warnings, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { useCostLabel } from '@/lib/settings'

export type ProductRow = {
  offer_id: string
  name: string
  sku: string
  qty: number
  returned_qty: number
  revenue: string
  avg_price: string | null
  commission: string
  logistics: string
  other: string
  total: string
  by_posting: string
  cost: string | null
  unit_cost: string | null
  cost_missing: boolean
  profit: string | null
  margin: string | null
  by_type: Record<string, string>
}

type ProductsPage = {
  data_source: DataSource
  coverage: Coverage
  cost_basis: 'with_vat' | 'without_vat'
  supplier_vat: string
  items: ProductRow[]
  total: number
  limit: number
  offset: number
  totals: {
    products: number
    qty: number
    revenue: string
    commission: string
    logistics: string
    other: string
    total: string
    cost: string
    profit: string
    margin: string | null
    missing_cost: number
  }
  unallocated: { total: string; lines: number; multi_product: string; by_type: Record<string, string> }
  period_total: string
  warnings: string[]
}

type Sort = 'revenue' | 'profit' | 'margin' | 'qty'
const PAGE = 50
/** Порог «низкой маржи» — как в акциях; станет настройкой на шаге 7.1. */
const LOW_MARGIN = 10

export const marginTone = (m: string | null) =>
  m === null ? 'text-muted' : Number(m) < 0 ? 'text-bad' : Number(m) <= LOW_MARGIN ? 'text-warn' : 'text-ok'

const signTone = (v: string) => (Number(v) < 0 ? 'text-bad' : Number(v) > 0 ? 'text-ok' : 'text-muted')

/** Прибыль по товарам: таблица с сортировкой, поиском, страницами, экспортом CSV и карточкой товара. */
export function ProductsView({ query, showSource }: { query: Record<string, string>; showSource: boolean }) {
  const costLabel = useCostLabel()
  const [sort, setSort] = useState<Sort>('revenue')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<ProductsPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<ProductRow | 'unallocated' | null>(null)
  const seq = useRef(0)
  const key = JSON.stringify(query)

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchText.trim())
      setOffset(0)
    }, 350)
    return () => clearTimeout(t)
  }, [searchText])

  useEffect(() => {
    const id = ++seq.current
    setBusy(true)
    api
      .get<ProductsPage>('/pnl/by-product', { params: { ...query, sort, order, limit: PAGE, offset, ...(search ? { search } : {}) } })
      .then((r) => {
        if (id !== seq.current) return
        setData(r.data)
        setError(null)
      })
      .catch((err) => id === seq.current && setError(errorMessage(err)))
      .finally(() => id === seq.current && setBusy(false))
    // query сравниваем по содержимому (key), а не по ссылке
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, sort, order, offset, search])

  function sortBy(s: Sort) {
    if (s === sort) setOrder(order === 'desc' ? 'asc' : 'desc')
    else {
      setSort(s)
      setOrder('desc')
    }
    setOffset(0)
  }

  async function exportCsv() {
    try {
      await saveFile(
        api.get('/pnl/by-product/export', { params: { ...query, sort, order, ...(search ? { search } : {}) }, responseType: 'blob' }),
        'products-pnl.csv',
      )
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  if (error && !data) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>

  const costTitle = data.cost_basis === 'without_vat' ? 'Себестоимость без НДС' : costLabel
  const t = data.totals
  const periodProfit = (Number(t.profit) + Number(data.unallocated.total)).toFixed(2)
  const empty = t.products === 0 && Number(data.unallocated.total) === 0
  const th = 'px-3 py-2 font-normal whitespace-nowrap'
  const SortTh = ({ s, children }: { s: Sort; children: React.ReactNode }) => (
    <th className={`${th} text-right`} aria-sort={sort === s ? (order === 'desc' ? 'descending' : 'ascending') : undefined}>
      <button type="button" onClick={() => sortBy(s)} className={`hover:text-fg ${sort === s ? 'text-fg' : ''}`} data-testid={`sort-${s}`}>
        {children} {sort === s ? (order === 'desc' ? '↓' : '↑') : '↕'}
      </button>
    </th>
  )

  return (
    <div className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy}>
      {showSource && <SourceNote source={data.data_source} coverage={data.coverage} />}
      <FormError message={error} />
      {empty ? (
        <section className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="products-empty">
          За этот период начислений нет — товаров не показать. Выберите другой период или загрузите отчёт по начислениям.
        </section>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4" data-testid="products-summary">
            <Stat label="Товаров" value={String(t.products)} hint={`продано ${t.qty} шт.`} />
            <Stat label="Выручка" value={rub(t.revenue)} hint="строки «Выручка»" />
            <Stat
              label="Прибыль по товарам"
              value={rub(t.profit)}
              hint={t.margin === null ? 'до налога' : `до налога, маржа ${pctText(t.margin)}`}
              tone={signTone(t.profit)}
            />
            <button type="button" onClick={() => setSelected('unallocated')} className="bg-card px-4 py-3 text-left hover:bg-line/40" data-testid="unallocated-stat">
              <div className="text-[11px] uppercase tracking-wider text-muted">Не распределено</div>
              <div className={`mt-1 text-lg font-bold tabular-nums ${signTone(data.unallocated.total)}`}>{rub(data.unallocated.total)}</div>
              <div className="mt-0.5 text-xs text-faint">хранение, подписки и др. — подробнее</div>
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Поиск по артикулу или названию"
              aria-label="Поиск товара"
              className="w-full max-w-xs rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent"
              data-testid="products-search"
            />
            <span className="text-xs text-muted">
              {search ? `найдено ${data.total}` : `${data.total} товаров`}
              {t.missing_cost > 0 && (
                <>
                  {' · '}
                  <span className="text-warn">без себестоимости — {t.missing_cost}</span>
                </>
              )}
            </span>
            <button type="button" onClick={exportCsv} className="ml-auto rounded-lg border border-line px-3 py-2 text-sm hover:border-accent hover:text-accent" data-testid="products-export">
              Скачать CSV
            </button>
          </div>

          <section className="overflow-hidden rounded-xl border border-line bg-card">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[60rem] text-[13px]" data-testid="products-table">
                <thead className="border-b border-line text-left text-xs text-muted">
                  <tr>
                    <th className={th}>Товар</th>
                    <SortTh s="qty">Шт.</SortTh>
                    <SortTh s="revenue">Выручка</SortTh>
                    <th className={`${th} text-right`}>Ср. цена</th>
                    <th className={`${th} text-right`}>Комиссия</th>
                    <th className={`${th} text-right`}>Логистика</th>
                    <th className={`${th} text-right`} title="Баллы за скидки, эквайринг, возвраты, продвижение и др.">
                      Прочее
                    </th>
                    <th className={`${th} text-right`}>{costTitle}</th>
                    <SortTh s="profit">Прибыль</SortTh>
                    <SortTh s="margin">Маржа</SortTh>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr
                      key={r.offer_id}
                      onClick={() => setSelected(r)}
                      className="cursor-pointer border-b border-line last:border-b-0 hover:bg-line/40"
                      data-testid="product-row"
                    >
                      <td className="max-w-[13rem] px-3 py-2">
                        <div className="truncate font-medium">{r.offer_id}</div>
                        {r.name && <div className="truncate text-xs text-muted">{r.name}</div>}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {r.qty}
                        {r.returned_qty > 0 && <span className="text-xs text-warn"> −{r.returned_qty}</span>}
                      </td>
                      <Num v={r.revenue} />
                      <Num v={r.avg_price} muted />
                      <Num v={r.commission} />
                      <Num v={r.logistics} />
                      <Num v={r.other} />
                      <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${r.cost_missing ? 'text-warn' : ''}`}>
                        {r.cost_missing ? 'нет' : rub(r.cost)}
                      </td>
                      <td className={`whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums ${r.profit === null ? 'text-muted' : signTone(r.profit)}`}>
                        {rub(r.profit)}
                      </td>
                      <td className={`whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums ${marginTone(r.margin)}`} data-testid="margin">
                        {pctText(r.margin)}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="border-t border-line text-sm">
                  <tr className="bg-line/40 font-semibold" data-testid="totals-row">
                    <td className="px-3 py-2">Итого по товарам</td>
                    <td className="px-3 py-2 text-right tabular-nums">{t.qty}</td>
                    <Num v={t.revenue} />
                    <td />
                    <Num v={t.commission} />
                    <Num v={t.logistics} />
                    <Num v={t.other} />
                    <Num v={t.cost} />
                    <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${signTone(t.profit)}`}>{rub(t.profit)}</td>
                    <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${marginTone(t.margin)}`}>{pctText(t.margin)}</td>
                  </tr>
                  <tr onClick={() => setSelected('unallocated')} className="cursor-pointer text-muted hover:bg-line/40" data-testid="unallocated-row">
                    <td className="px-3 py-2" colSpan={8}>
                      − Не распределено: начисления без товара — хранение, подписки и др. ({data.unallocated.lines} строк) →
                    </td>
                    <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${signTone(data.unallocated.total)}`}>
                      {rub(data.unallocated.total)}
                    </td>
                    <td />
                  </tr>
                  {t.missing_cost === 0 && (
                    <tr className="font-semibold" data-testid="period-profit-row">
                      <td className="px-3 py-2" colSpan={8}>
                        = Прибыль периода до налога (как на вкладке P&amp;L)
                      </td>
                      <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${signTone(periodProfit)}`}>{rub(periodProfit)}</td>
                      <td />
                    </tr>
                  )}
                </tfoot>
              </table>
            </div>
            {data.total > PAGE && (
              <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2 text-sm">
                <span className="text-muted">
                  {data.offset + 1}–{Math.min(data.offset + PAGE, data.total)} из {data.total}
                </span>
                <button type="button" disabled={data.offset === 0} onClick={() => setOffset(Math.max(0, data.offset - PAGE))} className="ml-auto rounded-md border border-line px-3 py-1 disabled:opacity-40" data-testid="page-prev">
                  ← Назад
                </button>
                <button type="button" disabled={data.offset + PAGE >= data.total} onClick={() => setOffset(data.offset + PAGE)} className="rounded-md border border-line px-3 py-1 disabled:opacity-40" data-testid="page-next">
                  Дальше →
                </button>
              </div>
            )}
          </section>
          <p className="text-xs text-faint">
            Прибыль по товару — до налога: всё, что Ozon начислил по товару, минус {costTitle.charAt(0).toLowerCase() + costTitle.slice(1)} проданного. Налог и НДС
            считаются на весь период — вкладка «P&amp;L». Маржа: красным — в минус, жёлтым — до {LOW_MARGIN} %, зелёным — выше.
          </p>
          <Warnings items={data.warnings} />
        </>
      )}
      {selected && <ProductCard data={data} row={selected} costTitle={costTitle} onClose={() => setSelected(null)} />}
    </div>
  )
}

function Stat({ label, value, hint, tone = '' }: { label: string; value: string; hint: string; tone?: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone}`}>{value}</div>
      <div className="mt-0.5 text-xs text-faint">{hint}</div>
    </div>
  )
}

function Num({ v, muted }: { v: string | null; muted?: boolean }) {
  return (
    <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${muted ? 'text-muted' : ''}`}>
      {rub(v)}
    </td>
  )
}

/** Карточка товара (или «Не распределено»): начисления по типам, себестоимость, прибыль. */
function ProductCard({
  data,
  row,
  costTitle,
  onClose,
}: {
  data: ProductsPage
  row: ProductRow | 'unallocated'
  costTitle: string
  onClose: () => void
}) {
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    close.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const un = row === 'unallocated'
  const byType = un ? data.unallocated.by_type : row.by_type
  const title = un ? 'Не распределено' : row.offer_id
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-bg/70 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-t-xl border border-line bg-card p-5 shadow-xl sm:rounded-xl"
        data-testid="product-card"
      >
        <div className="flex items-start gap-3">
          <div className="min-w-0">
            <h2 className="break-words text-lg font-semibold">{title}</h2>
            {!un && (row.name || row.sku) && (
              <p className="text-sm text-muted">
                {row.name}
                {row.sku && <span className="text-faint"> · SKU {row.sku}</span>}
              </p>
            )}
          </div>
          <button ref={close} type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-muted hover:text-fg" aria-label="Закрыть">
            ✕
          </button>
        </div>

        {un ? (
          <p className="mt-3 text-sm text-muted">
            Начисления, у которых нет товара: хранение, подписки, страхование, продвижение по кампаниям
            {Number(data.unallocated.multi_product) !== 0 && ', отправления с несколькими товарами'}. Они относятся ко всему магазину — мы
            не делим их между товарами, чтобы не исказить прибыль каждого. В P&amp;L периода они учтены.
          </p>
        ) : (
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Продано</dt>
            <dd className="text-right tabular-nums">
              {row.qty} шт.{row.avg_price && <span className="text-muted"> по {rub(row.avg_price)}</span>}
            </dd>
            {row.returned_qty > 0 && (
              <>
                <dt className="text-muted">Возвращено</dt>
                <dd className="text-right tabular-nums text-warn">{row.returned_qty} шт.</dd>
              </>
            )}
            <dt className="text-muted">Начислено Ozon</dt>
            <dd className={`text-right tabular-nums ${signTone(row.total)}`}>{rub(row.total)}</dd>
            <dt className="text-muted">{costTitle}</dt>
            <dd className={`text-right tabular-nums ${row.cost_missing ? 'text-warn' : ''}`}>
              {row.cost_missing ? 'не указана' : `${rub(row.cost)}${row.unit_cost ? ` (${rub(row.unit_cost)} × ${row.qty})` : ''}`}
            </dd>
            <dt className="font-semibold">Прибыль до налога</dt>
            <dd className={`text-right font-semibold tabular-nums ${row.profit === null ? 'text-muted' : signTone(row.profit)}`}>
              {rub(row.profit)}
              {row.margin !== null && <span className={`ml-1 text-xs ${marginTone(row.margin)}`}>{pctText(row.margin)}</span>}
            </dd>
          </dl>
        )}

        {!un && row.cost_missing && (
          <p className="mt-3 rounded-lg bg-warn/10 px-3 py-2 text-sm">
            Себестоимость не указана — прибыль не считаем, чтобы не показать завышенную.{' '}
            <Link href="/dashboard/costs?missing=1" className="text-accent hover:underline">
              Указать себестоимость →
            </Link>
          </p>
        )}
        {!un && row.returned_qty > 0 && row.unit_cost && (
          <p className="mt-3 text-xs text-faint">
            Себестоимость возвращённых товаров не вычитается — как в P&amp;L: возврат мог относиться к продаже прошлого периода. Если
            товар вернулся на склад, прибыль выше примерно на {rub(String(Number(row.unit_cost) * row.returned_qty))}.
          </p>
        )}

        <h3 className="mt-5 text-xs uppercase tracking-wider text-muted">Начисления по типам</h3>
        <table className="mt-1 w-full text-sm" data-testid="card-by-type">
          <tbody>
            {Object.entries(byType).map(([name, v]) => (
              <tr key={name} className="border-b border-line last:border-b-0">
                <td className="py-1.5 pr-3">{name}</td>
                <td className={`whitespace-nowrap py-1.5 text-right tabular-nums ${signTone(v)}`}>{rub(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!un && Number(row.by_posting) !== 0 && (
          <p className="mt-2 text-xs text-faint">
            Из них {rub(row.by_posting)} — строки без артикула, отнесены к товару по номеру отправления (в нём только этот товар).
          </p>
        )}
      </div>
    </div>
  )
}
