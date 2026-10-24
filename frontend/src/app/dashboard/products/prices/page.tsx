'use client'
// Цены (шаг 4.2): цена, минимальная и целевая, маржа и маржа в акции по каждому товару; подсказки «что поднять».
// Здесь только расчёт; цену в Ozon меняют в панели товара (6.2) и на странице «Цены списком» (6.3).
// 10.15: вкладки подраздела (цены, списком, история), «а если поставить …» — в расчёте цены панели.
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PriceSettingsForm } from '@/components/price-calc'
import { PricesTabs } from '@/components/prices-tabs'
import { ProductPanel } from '@/components/product-panel'
import { Badge, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { PRICE_FILTERS, type PriceFilter, type PriceRow, type PricesPage, type Suggestion, type Suggestions } from '@/lib/pricing'
import { marginTone } from '@/lib/products'
import { useLowMargin, useOnPriceSettingsChange } from '@/lib/settings'

type Sort = 'offer_id' | 'price' | 'min_price' | 'target_price' | 'margin' | 'promo_margin'

const PAGE = 50
const ALL: PriceFilter[] = PRICE_FILTERS.map((f) => f.value)
const num = (v: string) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })

export default function PricesRoute() {
  return (
    <Suspense>
      <Prices />
    </Suspense>
  )
}

function Prices() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const filters = (params.get('filter') ?? '').split(',').filter((f): f is PriceFilter => (ALL as string[]).includes(f))
  const selected = params.get('product')
  const setUrl = useCallback(
    (next: { filter?: PriceFilter[]; product?: string | null }) => {
      const q = new URLSearchParams(params.toString())
      if (next.filter !== undefined) {
        if (next.filter.length) q.set('filter', next.filter.join(','))
        else q.delete('filter')
      }
      if (next.product !== undefined) {
        if (next.product) q.set('product', next.product)
        else q.delete('product')
      }
      const s = q.toString()
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false })
    },
    [params, pathname, router],
  )

  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<Sort>('offer_id')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<PricesPage | null>(null)
  const [tips, setTips] = useState<Suggestions | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [editing, setEditing] = useState(false)
  const seq = useRef(0)
  const filterKey = filters.join(',')

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchText.trim())
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [searchText])

  useEffect(() => {
    const id = ++seq.current
    setBusy(true)
    api
      .get<PricesPage>('/price', {
        params: { sort, order, limit: PAGE, offset, ...(search ? { search } : {}), ...(filterKey ? { filters: filterKey } : {}) },
      })
      .then((r) => {
        if (id !== seq.current) return
        setData(r.data)
        setError(null)
      })
      .catch((err) => id === seq.current && setError(errorMessage(err)))
      .finally(() => id === seq.current && setBusy(false))
  }, [sort, order, offset, search, filterKey, reload])

  useEffect(() => {
    api
      .get<Suggestions>('/price/suggestions')
      .then((r) => setTips(r.data))
      .catch(() => setTips(null)) // подсказки — дополнение, без них таблица работает
  }, [reload])

  const reloadAll = useCallback(() => setReload((n) => n + 1), [])
  useOnPriceSettingsChange(reloadAll)
  const closePanel = useCallback(() => setUrl({ product: null }), [setUrl])

  function toggle(f: PriceFilter) {
    setOffset(0)
    setUrl({ filter: filters.includes(f) ? filters.filter((x) => x !== f) : [...filters, f] })
  }

  function sortBy(s: Sort, first: 'asc' | 'desc') {
    if (s === sort) setOrder(order === 'desc' ? 'asc' : 'desc')
    else {
      setSort(s)
      setOrder(first)
    }
    setOffset(0)
  }

  const open = (id: string) => setUrl({ product: id })
  const th = 'whitespace-nowrap px-3 py-2 font-normal'
  const SortTh = ({ s, first, left, children, title }: { s: Sort; first: 'asc' | 'desc'; left?: boolean; children: React.ReactNode; title?: string }) => (
    <th className={`${th} ${left ? '' : 'text-right'}`} aria-sort={sort === s ? (order === 'desc' ? 'descending' : 'ascending') : undefined} title={title}>
      <button type="button" onClick={() => sortBy(s, first)} className={`hover:text-fg ${sort === s ? 'text-fg' : ''}`} data-testid={`sort-${s}`}>
        {children} {sort === s ? (order === 'desc' ? '↓' : '↑') : <span className="text-faint">↕</span>}
      </button>
    </th>
  )

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Цены</h1>
        <p className="mt-1 text-sm text-muted">
          Минимальная цена — без прибыли и без убытка, целевая — с вашей маржой. Нажмите на товар — расчёт по шагам, «а если поставить
          …» и изменение цены в Ozon; многих сразу —{' '}
          <Link href="/dashboard/products/prices/bulk" className="text-accent hover:underline" data-testid="bulk-link">
            Цены списком →
          </Link>
        </p>
      </div>
      <PricesTabs />
      <FormError message={error} />

      {data && (
        <section className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="price-params">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>
              Реклама <b className="tabular-nums">{num(data.settings.ad_pct)} %</b> · целевая маржа{' '}
              <b className="tabular-nums">{num(data.settings.target_margin_pct)} %</b>
              {data.settings.defaults && <span className="text-warn"> — по умолчанию</span>}
            </span>
            {data.tax.known ? (
              <span className="text-muted">Налог: {data.tax.title}</span>
            ) : (
              <span className="text-warn">
                Налог не задан —{' '}
                <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                  указать →
                </Link>
              </span>
            )}
            <button type="button" onClick={() => setEditing(!editing)} className="ml-auto text-accent hover:underline" data-testid="price-params-edit">
              {editing ? 'Свернуть' : 'Изменить'}
            </button>
          </div>
          <p className="mt-1 text-xs text-faint">
            Комиссия и эквайринг — из Ozon по товару, логистика — по фактическим начислениям товара за 30–90 дней
            {data.shop_logistics.per_unit && `; товары без продаж — средняя по магазину ${rub(data.shop_logistics.per_unit)} на штуку`}.
          </p>
          {editing && (
            <div className="mt-3 border-t border-line pt-3">
              <PriceSettingsForm
                onSaved={() => {
                  setEditing(false)
                  reloadAll()
                }}
              />
            </div>
          )}
        </section>
      )}

      {tips && (tips.loss_count > 0 || tips.underpriced_count > 0) && (
        <div className="grid gap-3 lg:grid-cols-2" data-testid="price-suggestions">
          <TipList
            title="Ниже минимальной — каждая продажа в минус"
            count={tips.loss_count}
            items={tips.loss}
            tone="text-bad"
            onOpen={open}
            more={() => setUrl({ filter: ['below_min'] })}
            testid="tips-loss"
          />
          <TipList
            title="Можно поднять до целевой"
            count={tips.underpriced_count}
            items={tips.underpriced}
            tone="text-accent"
            onOpen={open}
            more={() => setUrl({ filter: ['below_target'] })}
            testid="tips-under"
          />
        </div>
      )}

      {!data ? (
        !error && <p className="text-sm text-muted">Загрузка…</p>
      ) : data.counts.all === 0 ? (
        <section className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="prices-empty">
          Товаров пока нет — они загружаются из Ozon по API-ключу. Как появятся, здесь будут минимальная и целевая цены.
        </section>
      ) : (
        <div className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy}>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Фильтры">
            {PRICE_FILTERS.map((f) => {
              const on = filters.includes(f.value)
              return (
                <button
                  key={f.value}
                  type="button"
                  title={f.hint}
                  aria-pressed={on}
                  onClick={() => toggle(f.value)}
                  className={`rounded-full border px-3 py-1 text-sm ${on ? 'border-accent bg-accent text-white' : 'border-line bg-card text-muted hover:border-accent hover:text-fg'}`}
                  data-testid={`filter-${f.value}`}
                >
                  {f.label} <span className={on ? '' : f.value === 'below_min' && data.counts.below_min ? 'text-bad' : 'text-faint'}>{data.counts[f.value]}</span>
                </button>
              )
            })}
            {filters.length > 0 && (
              <button type="button" onClick={() => setUrl({ filter: [] })} className="px-2 text-sm text-muted hover:text-fg">
                Сбросить
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Артикул, название или SKU"
              aria-label="Поиск товара"
              className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent sm:max-w-xs"
              data-testid="prices-search"
            />
            <span className="text-xs text-muted" data-testid="prices-found">
              {search || filters.length ? `найдено ${data.total}` : `${data.total} товаров`}
            </span>
          </div>

          {data.total === 0 ? (
            <section className="rounded-xl border border-line bg-card p-6 text-sm text-muted">Под выбранные фильтры товаров нет.</section>
          ) : (
            <section className="overflow-hidden rounded-xl border border-line bg-card">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[46rem] text-[13px]" data-testid="prices-table">
                  <thead className="border-b border-line text-left text-xs text-muted">
                    <tr>
                      <SortTh s="offer_id" first="asc" left>
                        Товар
                      </SortTh>
                      <SortTh s="price" first="desc" title="Ваша цена в Ozon">
                        Цена
                      </SortTh>
                      <SortTh s="min_price" first="desc" title="Ниже неё каждая продажа — в минус">
                        Мин.
                      </SortTh>
                      <SortTh s="target_price" first="desc" title="Цена с вашей целевой маржой">
                        Целевая
                      </SortTh>
                      <SortTh s="margin" first="asc" title="При текущей цене, до налога на прибыль">
                        Маржа
                      </SortTh>
                      <SortTh s="promo_margin" first="asc" title="По самой низкой цене в текущих акциях">
                        В акции
                      </SortTh>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((r) => (
                      <Row key={r.id} r={r} selected={selected === r.id} onOpen={() => open(r.id)} />
                    ))}
                  </tbody>
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
          )}
          <p className="text-xs text-faint" data-testid="prices-notes">
            Минимальная цена = (себестоимость с НДС + логистика + хранение + прочее) / (1 − комиссия − эквайринг − реклама − налоги с
            выручки). Целевая — то же, но в знаменателе ещё и маржа. Цена ниже минимальной — красным. Нажмите на товар — расчёт по шагам,
            свои значения и «проверить цену».
          </p>
        </div>
      )}

      {selected && <ProductPanel productId={selected} onClose={closePanel} onChanged={reloadAll} />}
    </div>
  )
}

const td = 'whitespace-nowrap px-3 py-2 text-right tabular-nums'

function Row({ r, selected, onOpen }: { r: PriceRow; selected: boolean; onOpen: () => void }) {
  const low = useLowMargin()
  const why = [...r.missing, ...r.errors].join(' ')
  return (
    <tr
      onClick={onOpen}
      className={`cursor-pointer border-b border-l-2 border-line last:border-b-0 hover:bg-line/40 ${r.below_min ? 'border-l-bad bg-bad/5' : 'border-l-transparent'} ${selected ? 'bg-accent/10' : ''}`}
      data-testid="price-row"
      data-below-min={r.below_min ? 'true' : undefined}
    >
      <td className="max-w-[16rem] px-3 py-2">
        <div className="truncate font-medium">{r.offer_id}</div>
        <div className="truncate text-xs text-muted">{r.name ?? 'Без названия'}</div>
      </td>
      <td className={`${td} ${r.below_min ? 'font-semibold text-bad' : ''}`} data-testid="price-cell">
        {rub(r.price)}
        {r.below_min && r.min_price && r.price && <div className="text-[11px] font-normal">−{rub(String(Number(r.min_price) - Number(r.price)))} до мин.</div>}
      </td>
      <td className={`${td} ${r.min_price === null ? 'text-warn' : ''}`} title={why || undefined}>
        {r.min_price !== null ? rub(r.min_price) : r.missing.length ? 'нет данных' : 'недостижима'}
        {r.logistics_source === 'shop' && <div className="text-[11px] text-faint">логистика — оценка</div>}
      </td>
      <td className={`${td} ${r.below_target ? 'text-accent' : ''}`} title={r.errors.join(' ') || undefined}>
        {rub(r.target_price)}
      </td>
      <td className={`${td} font-medium ${marginTone(r.margin, low)}`} data-testid="price-margin">
        {pctText(r.margin)}
        {r.unit_profit && <div className="text-[11px] font-normal">{rub(r.unit_profit)} с шт.</div>}
      </td>
      <td className={`${td} max-w-[11rem]`} title={r.promo ? `${r.promo.title}: ${rub(r.promo.action_price)}` : undefined}>
        {r.promo ? (
          <>
            <div className={`font-medium ${marginTone(r.promo.margin, low)}`}>{pctText(r.promo.margin)}</div>
            <div className="truncate text-[11px] text-faint">
              {rub(r.promo.action_price)}
              {r.promo.count > 1 && ` · акций ${r.promo.count}`}
              {r.promo.added_automatically && (
                <>
                  {' '}
                  <Badge tone="warn" title="Ozon добавил товар в акцию без вас">
                    авто
                  </Badge>
                </>
              )}
            </div>
          </>
        ) : (
          <span className="text-faint">—</span>
        )}
      </td>
    </tr>
  )
}

function TipList({
  title,
  count,
  items,
  tone: color,
  onOpen,
  more,
  testid,
}: {
  title: string
  count: number
  items: Suggestion[]
  tone: string
  onOpen: (id: string) => void
  more: () => void
  testid: string
}) {
  return (
    <section className="min-w-0 rounded-xl border border-line bg-card p-4" data-testid={testid}>
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className={`text-sm tabular-nums ${count ? color : 'text-faint'}`}>{count}</span>
        {count > items.length && (
          <button type="button" onClick={more} className="ml-auto text-xs text-accent hover:underline">
            Все →
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted">Таких товаров нет.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line text-sm">
          {items.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => onOpen(s.id)} className="flex w-full items-baseline gap-3 py-1.5 text-left hover:text-accent">
                <span className="min-w-0 flex-1 truncate">{s.offer_id}</span>
                <span className="whitespace-nowrap tabular-nums text-muted">{rub(s.price)} →</span>
                <span className={`whitespace-nowrap font-medium tabular-nums ${color}`}>{rub(s.raise_to)}</span>
                <span className="w-14 whitespace-nowrap text-right text-xs tabular-nums text-faint">+{num(s.raise_pct)} %</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
