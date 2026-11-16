'use client'
// ABC-анализ (шаг 10.10): группы A, B, C по выручке, прибыли или продажам за период — сколько товаров и какой вклад,
// сравнение ABC по выручке и по прибыли (матрица 3 × 3, ячейка → товары в таблице), таблица с долей, накопленной долей,
// прошлым периодом и сменой группы, Excel. Данные — GET /analytics/abc, /analytics/abc/export; метод — engine/abc.
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

import { CopyText } from '@/components/copy'
import { FormError } from '@/components/AuthForm'
import { CheckOption, ChipOptions, FilterBar, FilterButton, FilterField, SearchBox } from '@/components/filters'
import { type SortValue, SortTh, num, sortRows } from '@/components/table'
import { type AnalyticsFilter, formatMetric } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'

export type Criterion = 'revenue' | 'profit' | 'qty'
type Group = 'A' | 'B' | 'C'
type Ref = { id: string | null; offer_id: string; name: string | null }
type AbcItem = {
  product: Ref
  group: Group | null
  value: string | null
  rank: number | null
  share: string | null
  cum_share: string | null
  loss: boolean
  prev_value: string | null
  prev_group: Group | null
  change_pct: string | null
  revenue_group: Group | null
  profit_group: Group | null
  profit_loss: boolean
}
export type AbcData = {
  date_from: string
  date_to: string
  criterion: Criterion
  bounds: { a: number; b: number }
  data_source: string
  previous: { date_from: string; date_to: string; has_data: boolean }
  total: string
  groups: { group: Group; count: number; value: string; share: string; product_share: string }[]
  loss: { count: number; value: string }
  unknown: number
  items: AbcItem[]
  matrix: { revenue_group: Group; profit_group: Group | null; count: number }[]
}

export const CRITERIA: { value: Criterion; label: string; unit: string }[] = [
  { value: 'revenue', label: 'Выручка', unit: 'выручки' },
  { value: 'profit', label: 'Прибыль', unit: 'прибыли' },
  { value: 'qty', label: 'Продажи, шт.', unit: 'продаж' },
]
const GROUPS: Group[] = ['A', 'B', 'C']
/** Цвет группы — токенами темы: A — главные, B — средние, C — остальные. */
const TONE: Record<Group, { bar: string; badge: string }> = {
  A: { bar: 'bg-ok/70', badge: 'bg-ok/15 text-ok' },
  B: { bar: 'bg-accent/70', badge: 'bg-accent/15 text-accent' },
  C: { bar: 'bg-faint/50', badge: 'bg-line text-muted' },
}
const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
const pct = (v: string | number | null) =>
  v === null ? '—' : `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`.replace('-', '−')
const fmt = (v: string | null, c: Criterion) => formatMetric(v, c === 'qty' ? 'qty' : 'rub')

export function abcParams(f: AnalyticsFilter, by: Criterion) {
  return {
    date_from: f.range.from,
    date_to: f.range.to,
    by,
    ...(f.product ? { product_id: f.product } : {}),
    ...(f.category !== null ? { category_id: String(f.category) } : {}),
  }
}

export function useAbc(filter: AnalyticsFilter, by: Criterion) {
  const [state, setState] = useState<{ data: AbcData | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true })
  const key = JSON.stringify(abcParams(filter, by))
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, error: null, loading: true }))
    api
      .get<AbcData>('/analytics/abc', { params: JSON.parse(key) })
      .then((r) => alive && setState({ data: r.data, error: null, loading: false }))
      .catch((err) => alive && setState({ data: null, error: errorMessage(err), loading: false }))
    return () => {
      alive = false
    }
  }, [key])
  return state
}

export function GroupBadge({ g, title }: { g: Group | null; title?: string }) {
  if (g === null) return <span className="text-faint" title={title ?? 'нет данных'}>—</span>
  return (
    <span className={`inline-block w-6 rounded text-center text-xs font-semibold ${TONE[g].badge}`} title={title} data-testid="abc-badge">
      {g}
    </span>
  )
}

/** Группы: сколько товаров и какой вклад; клик — товары группы в таблице. */
export function AbcGroups({ data, onPick, picked }: { data: AbcData; onPick: (g: Group | 'loss') => void; picked: string }) {
  const c = CRITERIA.find((x) => x.value === data.criterion)!
  const b = data.bounds
  const text: Record<Group, string> = {
    A: `первые ${b.a} % ${c.unit} — главные товары`,
    B: `от ${b.a} до ${b.b} % ${c.unit}`,
    C: `остальные ${100 - b.b} %${data.criterion === 'profit' ? ', ноль и убыток' : ''}`,
  }
  return (
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3" data-testid="abc-groups">
      {data.groups.map((g) => (
        <button
          key={g.group}
          type="button"
          onClick={() => onPick(g.group)}
          aria-pressed={picked === g.group}
          className={`bg-card px-4 py-3 text-left hover:bg-line/40 ${picked === g.group ? 'ring-1 ring-inset ring-accent' : ''}`}
          data-testid={`abc-group-${g.group}`}
        >
          <div className="flex items-center gap-2">
            <GroupBadge g={g.group} />
            <span className="text-[11px] uppercase tracking-wider text-muted">{text[g.group]}</span>
          </div>
          <div className="mt-1 text-lg font-bold tabular-nums">
            {g.count} {g.count % 10 === 1 && g.count % 100 !== 11 ? 'товар' : [2, 3, 4].includes(g.count % 10) && ![12, 13, 14].includes(g.count % 100) ? 'товара' : 'товаров'}
          </div>
          <div className="mt-0.5 text-xs text-muted">
            {pct(g.product_share)} товаров · {fmt(g.value, data.criterion)} · <span className="text-fg">{pct(g.share)} {c.unit}</span>
          </div>
        </button>
      ))}
    </div>
  )
}

/** Диаграмма распределения: доля товаров и доля вклада каждой группы — две полосы друг под другом. */
export function AbcDistribution({ data, onPick }: { data: AbcData; onPick: (g: Group) => void }) {
  const c = CRITERIA.find((x) => x.value === data.criterion)!
  const bar = (label: string, key: 'product_share' | 'share', testid: string) => (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-3 text-sm sm:grid-cols-[7rem_minmax(0,1fr)]">
      <span className="text-muted">{label}</span>
      <div className="flex h-7 overflow-hidden rounded-md bg-line" data-testid={testid}>
        {data.groups
          .filter((g) => Number(g[key]) > 0)
          .map((g) => (
            <button
              key={g.group}
              type="button"
              onClick={() => onPick(g.group)}
              title={`${g.group}: ${pct(g[key])}`}
              className={`flex min-w-0 items-center justify-center text-xs font-semibold text-fg hover:opacity-80 ${TONE[g.group].bar}`}
              style={{ width: `${Number(g[key])}%` }}
            >
              <span className="truncate px-1">
                {Number(g[key]) >= 25 ? `${g.group} ${pct(g[key])}` : g.group}
              </span>
            </button>
          ))}
      </div>
    </div>
  )
  return (
    <section className="space-y-2 rounded-xl border border-line bg-card p-4" data-testid="abc-distribution">
      <h2 className="text-sm font-semibold">Сколько товаров и какой вклад</h2>
      {bar('Товары', 'product_share', 'abc-bar-products')}
      {bar(`Доля ${c.unit}`, 'share', 'abc-bar-share')}
      <p className="text-xs text-faint">
        Немного товаров A дают большую часть {c.unit}: их держите в наличии и следите за ценой в первую очередь. Нажмите на группу —
        её товары в таблице.
      </p>
    </section>
  )
}

const CELL_NOTE: Record<string, { text: string; tone: 'bad' | 'ok' | null }> = {
  AC: { text: 'продаётся много, зарабатывает мало', tone: 'bad' },
  BC: { text: 'продаётся заметно, зарабатывает мало', tone: 'bad' },
  CA: { text: 'продаж мало, но выгодно', tone: 'ok' },
  AA: { text: 'главные товары', tone: null },
}

/** Сравнение ABC по выручке и по прибыли: строки — выручка, столбцы — прибыль; клик по ячейке — её товары в таблице. */
export function AbcMatrix({ data, picked, onPick }: { data: AbcData; picked: string; onPick: (cell: string) => void }) {
  const count = (r: Group, p: Group | null) => data.matrix.find((c) => c.revenue_group === r && c.profit_group === p)?.count ?? 0
  const noCost = GROUPS.some((r) => count(r, null) > 0)
  const cols: (Group | null)[] = noCost ? [...GROUPS, null] : GROUPS
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="abc-matrix">
      <h2 className="text-sm font-semibold">Выручка и прибыль вместе</h2>
      <p className="mt-1 text-xs text-muted">Группа по выручке (строки) и по прибыли (столбцы) за тот же период. Нажмите на ячейку — её товары в таблице.</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[20rem] text-sm">
          <thead>
            <tr className="text-xs text-muted">
              <th className="px-2 py-1 text-left font-normal">Выручка ↓ · Прибыль →</th>
              {cols.map((p) => (
                <th key={p ?? 'none'} className="px-2 py-1 text-center font-normal">
                  {p === null ? <span title="Нет себестоимости — прибыль неизвестна">нет с/с</span> : <GroupBadge g={p} />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {GROUPS.map((r) => (
              <tr key={r}>
                <th className="px-2 py-1 text-left font-normal">
                  <GroupBadge g={r} />
                </th>
                {cols.map((p) => {
                  const n = count(r, p)
                  const key = `${r}${p ?? '-'}`
                  const note = CELL_NOTE[key]
                  const tone = note?.tone === 'bad' && n ? 'bg-bad/10' : note?.tone === 'ok' && n ? 'bg-ok/10' : ''
                  return (
                    <td key={key} className="p-1 text-center">
                      <button
                        type="button"
                        disabled={n === 0}
                        onClick={() => onPick(key)}
                        aria-pressed={picked === key}
                        title={note?.text}
                        className={`w-full rounded-md border px-2 py-2 tabular-nums disabled:cursor-default disabled:text-faint ${tone} ${picked === key ? 'border-accent text-accent' : 'border-line hover:border-accent'}`}
                        data-testid={`abc-cell-${key}`}
                      >
                        {n}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-2 space-y-0.5 text-xs text-muted">
        {count('A', 'C') + count('B', 'C') > 0 && (
          <li>
            <span className="text-bad">A или B по выручке, C по прибыли</span> — продаётся заметно, а зарабатывает мало: проверьте цену,
            комиссию, логистику и рекламу.
          </li>
        )}
        {count('C', 'A') > 0 && (
          <li>
            <span className="text-ok">C по выручке, A по прибыли</span> — продаж мало, но выгодно: возможно, стоит продвигать.
          </li>
        )}
      </ul>
    </section>
  )
}

type SortKey = 'rank' | 'offer_id' | 'group' | 'value' | 'share' | 'cum_share' | 'change_pct' | 'groups'
// значение колонки для сортировки (components/table.tsx): группа A → C, «нет с/с» — в конце
const BY: Record<SortKey, (i: AbcItem) => SortValue> = {
  rank: (i) => i.rank,
  offer_id: (i) => i.product.offer_id,
  group: (i) => i.group,
  value: (i) => num(i.value),
  share: (i) => num(i.share),
  cum_share: (i) => num(i.cum_share),
  change_pct: (i) => num(i.change_pct),
  groups: (i) => (i.revenue_group ? `${i.revenue_group}${i.profit_group ?? 'Z'}` : null),
}

/** Товары: группа, показатель, доля, накопленная доля, к прошлому периоду и смена группы, ABC по выручке и прибыли. */
export function AbcTable({ data, filter, pick, onPick }: { data: AbcData; filter: AnalyticsFilter; pick: string; onPick: (p: string) => void }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'rank', desc: false })
  const [error, setError] = useState<string | null>(null)
  const c = CRITERIA.find((x) => x.value === data.criterion)!
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    const picked = data.items.filter((i) => {
      if (q && !i.product.offer_id.toLowerCase().includes(q) && !(i.product.name ?? '').toLowerCase().includes(q)) return false
      if (pick === 'A' || pick === 'B' || pick === 'C') return i.group === pick
      if (pick === 'loss') return i.profit_loss
      if (pick.length === 2) return i.revenue_group === pick[0] && (i.profit_group ?? '-') === pick[1]
      return true
    })
    if (sort.key === 'rank') return sort.desc ? [...picked].reverse() : picked
    return sortRows(picked, sort.desc ? 'desc' : 'asc', BY[sort.key])
  }, [data.items, search, pick, sort])

  async function exportXlsx() {
    setError(null)
    try {
      await saveFile(api.get('/analytics/abc/export', { params: abcParams(filter, data.criterion), responseType: 'blob' }), 'abc.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не получилось выгрузить — попробуйте ещё раз'))
    }
  }

  const chips: { value: string; label: string; n: number }[] = [
    { value: 'all', label: 'Все', n: data.items.length },
    ...data.groups.map((g) => ({ value: g.group, label: g.group, n: g.count })),
    ...(data.items.some((i) => i.profit_loss) ? [{ value: 'loss', label: 'Убыточные', n: data.items.filter((i) => i.profit_loss).length }] : []),
  ]
  const cellChip = pick.length === 2 ? `Выручка ${pick[0]} · прибыль ${pick[1] === '-' ? 'нет с/с' : pick[1]}` : null
  const th = (key: SortKey, label: React.ReactNode, align: 'left' | 'right' | 'center' = 'left', title?: string) => (
    <SortTh
      active={sort.key === key ? (sort.desc ? 'desc' : 'asc') : null}
      onSort={(dir) => setSort(dir ? { key, desc: dir === 'desc' } : { key: 'rank', desc: false })}
      resettable={key !== 'rank'}
      kind={key === 'offer_id' || key === 'group' || key === 'groups' ? 'text' : 'number'}
      align={align}
      className="px-3 py-2 font-medium"
      title={title}
      testid={`abc-sort-${key}`}
    >
      {label}
    </SortTh>
  )
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="abc-table">
      <div className="space-y-2 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Товары</h2>
        <FilterBar
          end={
            <>
              <FilterButton count={pick !== 'all' ? 1 : 0} onReset={() => onPick('all')} testid="abc-filters">
                <FilterField label="Группа">
                  <ChipOptions
                    options={chips.map((f) => ({ value: f.value, label: f.label, count: f.n }))}
                    value={pick}
                    onChange={onPick}
                    testid="abc-pick"
                  />
                </FilterField>
                {cellChip && (
                  <FilterField label="Ячейка матрицы">
                    <CheckOption checked onChange={() => onPick('all')} label={cellChip} testid="abc-pick-cell" />
                  </FilterField>
                )}
              </FilterButton>
              <button type="button" onClick={exportXlsx} className="h-9 rounded-lg border border-line px-3 text-sm hover:border-accent hover:text-accent" data-testid="abc-export">
                Excel
              </button>
            </>
          }
        >
          <SearchBox value={search} onChange={setSearch} testid="abc-search" className="w-full sm:w-72" />
          {cellChip && <span className="text-xs text-muted">{cellChip}</span>}
        </FilterBar>
      </div>
      <FormError message={error} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              {th('rank', '№')}
              {th('offer_id', 'Товар')}
              {th('group', 'Группа', 'center')}
              {th('value', c.label, 'right')}
              {th('share', 'Доля', 'right')}
              {th('cum_share', 'Накопленная', 'right', 'Доля товаров выше и этого вместе')}
              {th('change_pct', 'К прошлому', 'right')}
              {th('groups', 'Выручка / прибыль', 'center', 'Группа по выручке и по прибыли за тот же период')}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((i) => (
              <tr key={i.product.offer_id} data-testid="abc-row" data-group={i.group ?? ''}>
                <td className="px-3 py-2 tabular-nums text-faint">{i.rank ?? ''}</td>
                <td className="max-w-[16rem] px-3 py-2">
                  {i.product.id ? (
                    <Link href={`/dashboard/products/${i.product.id}`} className="block min-w-0 font-medium hover:text-accent" data-testid="abc-product">
                      <CopyText value={i.product.offer_id} label="артикул" />
                    </Link>
                  ) : (
                    <CopyText value={i.product.offer_id} label="артикул" className="font-medium" />
                  )}
                  {i.product.name && <CopyText value={i.product.name} label="название" className="text-xs text-faint" />}
                </td>
                <td className="px-3 py-2 text-center">
                  <GroupBadge g={i.group} title={i.group === null ? 'нет себестоимости — прибыль неизвестна' : undefined} />
                  {i.loss && (
                    <span className="mt-0.5 block text-[11px] text-bad" data-testid="abc-loss">
                      убыток
                    </span>
                  )}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${i.loss ? 'text-bad' : ''}`}>
                  {i.value === null ? <span className="text-warn">нет с/с</span> : fmt(i.value, data.criterion)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(i.share)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{pct(i.cum_share)}</td>
                <td className="px-3 py-2 text-right tabular-nums" data-testid="abc-change">
                  <ChangeCell i={i} c={data.criterion} hasPrev={data.previous.has_data} />
                </td>
                <td className="px-3 py-2 text-center">
                  <span className="inline-flex gap-1">
                    <GroupBadge g={i.revenue_group} title="по выручке" />
                    <GroupBadge g={i.profit_group} title={i.profit_group === null ? 'нет себестоимости' : i.profit_loss ? 'по прибыли — убыток' : 'по прибыли'} />
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-center text-sm text-muted">
                  Нет товаров по этому фильтру
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="border-t border-line px-4 py-2 text-xs text-faint">
        Группа — по накопленной доле до товара: A — пока меньше {data.bounds.a} %, B — меньше {data.bounds.b} %, C — остальные; товар,
        на котором доля переходит границу, — ещё в старшей группе, равные — в одной.{' '}
        <Link href="/dashboard/settings#abc" className="text-accent hover:underline">
          Границы — в настройках
        </Link>
        . «К прошлому» — с {short(data.previous.date_from)} по {short(data.previous.date_to)}.
      </p>
    </section>
  )
}

function ChangeCell({ i, c, hasPrev }: { i: AbcItem; c: Criterion; hasPrev: boolean }) {
  if (!hasPrev) return <span className="text-faint">—</span>
  const moved = i.prev_group !== i.group
  const up = i.prev_group && i.group && i.group < i.prev_group // A < B < C
  return (
    <span className="inline-flex flex-col items-end">
      {i.change_pct !== null ? (
        <span className={Number(i.change_pct) > 0 ? 'text-ok' : Number(i.change_pct) < 0 ? 'text-bad' : 'text-muted'} title={`было ${fmt(i.prev_value, c)}`}>
          {Number(i.change_pct) > 0 ? '+' : ''}
          {pct(i.change_pct)}
        </span>
      ) : (
        <span className="text-faint" title={i.prev_value === null ? 'в прошлом периоде товара не было' : undefined}>
          —
        </span>
      )}
      {i.prev_group === null ? (
        <span className="text-[11px] text-accent">новый</span>
      ) : (
        moved && (
          <span className={`text-[11px] ${up ? 'text-ok' : 'text-bad'}`} data-testid="abc-moved">
            {i.prev_group} → {i.group ?? '—'}
          </span>
        )
      )}
    </span>
  )
}
