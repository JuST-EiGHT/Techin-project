'use client'
// Кандидаты на вывоз (шаг 5.1, BUSINESS_LOGIC §6): что, откуда и сколько вывезти со складов Ozon. Параметры отчёта
// сохраняются у пользователя; на странице их можно менять — отчёт пересчитывается сразу, сохранять не обязательно.
// 10.17: причина и приоритет, история продаж, замороженные деньги, фильтры (склад, приоритет, решение), решение
// «вывозим» / «оставить» с датой и переход к оформлению вывоза в кабинете Ozon (через API заявку не создать), список
// уже оформленных вывозов из Ozon.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { type StockProduct, type Thresholds, num, perDay, productHref } from '@/lib/stocks'

type Params = { total: number | null; keep_idle: number; keep_selling: number; excluded: string[] }

type Line = {
  warehouse_id: string
  warehouse: string
  cluster_key: string | null
  cluster: string | null
  present: number
  qty: number
  cluster_left: number
}

type Item = {
  product: StockProduct
  kind: 'idle' | 'selling'
  reason: string
  keep: number
  surplus: number | null
  available: number
  avg_daily_sales: string | null
  days_of_supply: string | null
  last_sale: string | null
  days_without_sales: number | null
  possible: number
  qty: number
  excluded_units: number
  lines: Line[]
  qty_all: number // по товару целиком — без фильтра склада
  reason_kind: 'no_sales' | 'low_turnover' | 'excess'
  reason_title: string
  priority: Priority
  sold_30: number | null
  sold_90: number | null
  unit_cost: string | null
  frozen: string | null
  decision: { decision: 'remove' | 'keep'; qty: number | null; at: string } | null
}

type Priority = 'high' | 'medium' | 'normal'
type DecisionKey = 'undecided' | 'remove' | 'keep'
type View = { warehouse_id: string; priority: Priority | ''; decision: DecisionKey | '' }

type Removal = {
  product_id: string | null
  offer_id: string
  name: string | null
  qty: number
  created_at: string | null
  state: string | null
  box_state: string | null
  warehouse: string | null
  destination: string | null
  is_auto_return: boolean
}
type Removals = { date_from: string; date_to: string; rows: Removal[]; qty: number; cabinet_url: string; cabinet_path: string }

type Report = {
  params: Params
  saved: Params
  is_saved: boolean
  thresholds: Thresholds
  sales_known: boolean
  stocks_known: boolean
  sales_since: string | null
  clusters: { key: string; name: string; present: number; products: number }[]
  possible: number
  planned: number
  left_out: number
  items: Item[]
  frozen: string | null
  without_cost: number
  warehouses: { id: string; name: string; cluster: string | null; qty: number; products: number }[]
  counts: { priority: Record<Priority, number>; decision: Record<DecisionKey, number> }
  shown: { items: number; qty: number; frozen: string | null; without_cost: number }
  cabinet_url: string
  cabinet_path: string
}

const PRIORITY: Record<Priority, { label: string; badge: string; hint: string }> = {
  high: { label: 'Высокий', badge: 'bg-bad/15 text-bad', hint: 'не продаётся — деньги лежат без движения' },
  medium: { label: 'Средний', badge: 'bg-warn/15 text-warn', hint: 'запаса больше двух порогов «Избыток»' },
  normal: { label: 'Обычный', badge: 'bg-accent/15 text-accent', hint: 'запаса больше порога «Избыток»' },
}
const DECISIONS: { key: DecisionKey; label: string }[] = [
  { key: 'undecided', label: 'Без решения' },
  { key: 'remove', label: 'Вывозим' },
  { key: 'keep', label: 'Оставить' },
]
const EMPTY_VIEW: View = { warehouse_id: '', priority: '', decision: '' }
const viewParams = (v: View) => Object.fromEntries(Object.entries(v).filter(([, x]) => x)) as Record<string, string>

type Form = { total: string; keep_idle: string; keep_selling: string; excluded: string[] }

const DEBOUNCE_MS = 400
const toForm = (p: Params): Form => ({
  total: p.total ? String(p.total) : '',
  keep_idle: String(p.keep_idle),
  keep_selling: String(p.keep_selling),
  excluded: p.excluded,
})

/** Параметры для запроса; ошибка — текст для пользователя. */
function query(f: Form): { params?: Record<string, string | number>; error?: string } {
  const ints = { total: f.total || '0', keep_idle: f.keep_idle, keep_selling: f.keep_selling }
  for (const [key, v] of Object.entries(ints)) {
    if (!/^\d+$/.test(v) || Number(v) > (key === 'total' ? 1_000_000 : 10_000))
      return { error: key === 'total' ? 'Вывезти всего — целое число штук или пусто' : 'Сколько оставлять — целое число штук от 0' }
  }
  return { params: { total: Number(ints.total), keep_idle: Number(ints.keep_idle), keep_selling: Number(ints.keep_selling), excluded: f.excluded.join(',') } }
}

const pieces = (n: number) => `${n.toLocaleString('ru-RU')} шт.`

export default function WithdrawalPage() {
  const [form, setForm] = useState<Form | null>(null)
  const [data, setData] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [view, setView] = useState<View>(EMPTY_VIEW)
  const [tick, setTick] = useState(0)
  const first = useRef(true)

  useEffect(() => {
    api
      .get<Report>('/warehouses/withdrawal')
      .then(({ data }) => {
        setData(data)
        setForm(toForm(data.params))
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])

  useEffect(() => {
    if (!form) return
    if (first.current) {
      first.current = false
      return
    }
    const q = query(form)
    if (q.error) return setError(q.error)
    setError(null)
    setBusy(true)
    let alive = true
    const timer = setTimeout(() => {
      api
        .get<Report>('/warehouses/withdrawal', { params: { ...q.params, ...viewParams(view) } })
        .then(({ data }) => alive && setData(data))
        .catch((err) => alive && setError(errorMessage(err)))
        .finally(() => alive && setBusy(false))
    }, DEBOUNCE_MS)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [form, view, tick])

  function change(next: Partial<Form>) {
    setSaved(false)
    setForm((f) => (f ? { ...f, ...next } : f))
  }

  async function save() {
    if (!form) return
    const q = query(form)
    if (q.error || !q.params) return setError(q.error ?? null)
    setError(null)
    try {
      const p = q.params
      await api.put('/warehouses/withdrawal/params', {
        total: p.total || null,
        keep_idle: p.keep_idle,
        keep_selling: p.keep_selling,
        excluded: form.excluded,
      })
      const { data } = await api.get<Report>('/warehouses/withdrawal', { params: { ...p, ...viewParams(view) } })
      setData(data)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  async function exportXlsx() {
    if (!form) return
    const q = query(form)
    if (q.error) return setError(q.error)
    setError(null)
    try {
      await saveFile(api.get('/warehouses/withdrawal/export', { params: { ...q.params, ...viewParams(view) }, responseType: 'blob' }), 'withdrawal.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function decide(it: Item, decision: DecisionKey) {
    setError(null)
    try {
      await api.post('/warehouses/withdrawal/decisions', { product_id: it.product.id, decision, qty: decision === 'undecided' ? null : it.qty_all })
      setTick((n) => n + 1)
    } catch (err) {
      setError(errorMessage(err, 'Решение не сохранилось — попробуйте ещё раз'))
    }
  }

  const t = data?.thresholds
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <Link href="/dashboard/warehouses" className="text-sm text-accent hover:underline">
          ← Склады и остатки
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">На вывоз</h1>
        <p className="mt-1 text-sm text-muted">
          Товары, которых на складах Ozon больше, чем продастся, — что, из какого кластера и склада и сколько забрать, чтобы
          не платить за хранение и вернуть деньги в оборот. Сначала — дольше всего не продававшиеся.
        </p>
      </div>

      <FormError message={error} />

      {form && data && t && (
        <section className="rounded-xl border border-line bg-card p-5" data-testid="withdrawal-params">
          <h2 className="text-lg font-semibold">Параметры</h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <NumField
              id="total"
              label="Вывезти всего"
              value={form.total}
              placeholder="без лимита"
              onChange={(v) => change({ total: v })}
              why="Сколько готовы забрать за раз — машина, место на своём складе. Пусто — всё, что можно; хуже продающиеся войдут первыми."
            />
            <NumField
              id="keep_idle"
              label="Оставлять в кластере — у товара без продаж"
              value={form.keep_idle}
              onChange={(v) => change({ keep_idle: v })}
              why="Чтобы товар оставался в продаже в этом регионе и быстро доезжал до покупателя."
            />
            <NumField
              id="keep_selling"
              label="— у продающегося"
              value={form.keep_selling}
              onChange={(v) => change({ keep_selling: v })}
              why={`Продающийся товар вывозим только сверх запаса на ${t.excess} дн. (порог «Избыток») и оставляем в кластере не меньше этого.`}
            />
          </div>
          <div className="mt-4">
            <div className="text-sm text-muted">Не трогать кластеры</div>
            <p className="text-xs text-faint">
              Например, кластер рядом с вашим складом или там, где хранение выгоднее, — из него ничего не вывозим.
            </p>
            {data.clusters.length === 0 ? (
              <p className="mt-2 text-sm text-muted">На складах Ozon сейчас ничего нет.</p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Не трогать кластеры">
                {data.clusters.map((c) => {
                  const on = form.excluded.includes(c.key)
                  return (
                    <button
                      key={c.key}
                      type="button"
                      aria-pressed={on}
                      onClick={() => change({ excluded: on ? form.excluded.filter((k) => k !== c.key) : [...form.excluded, c.key] })}
                      className={`rounded-lg border px-3 py-1.5 text-left text-sm ${on ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
                      data-testid={`cluster-${c.key}`}
                    >
                      <span className={on ? 'font-medium' : ''}>{on ? '✓ ' : ''}{c.name}</span>
                      <span className="ml-1.5 text-xs text-faint">{pieces(c.present)}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={save}
              disabled={data.is_saved}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              data-testid="withdrawal-save"
            >
              Сохранить параметры
            </button>
            {!data.is_saved && (
              <button
                type="button"
                onClick={() => {
                  setForm(toForm(data.saved))
                  setSaved(false)
                }}
                className="text-sm text-accent hover:underline"
              >
                Вернуть сохранённые
              </button>
            )}
            <span className="text-xs text-faint" data-testid="withdrawal-saved-note">
              {saved ? 'Сохранено — отчёт будет открываться с ними' : data.is_saved ? 'Параметры сохранены у вас в аккаунте' : 'Есть несохранённые изменения'}
            </span>
          </div>
        </section>
      )}

      {data && (
        <Result data={data} busy={busy} onExport={exportXlsx} view={view} onView={(next) => setView((v) => ({ ...v, ...next }))} onDecide={decide} />
      )}
      {data?.stocks_known && <Cabinet url={data.cabinet_url} path={data.cabinet_path} />}
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
    </div>
  )
}

function NumField({ id, label, value, onChange, why, placeholder }: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  why: string
  placeholder?: string
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm text-muted">
        {label}
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={id}
          inputMode="numeric"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
          className="w-36 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
          data-testid={`param-${id}`}
        />
        <span className="text-sm text-muted">шт.</span>
      </div>
      <p className="mt-1 text-xs text-faint">{why}</p>
    </div>
  )
}

function Result({
  data,
  busy,
  onExport,
  view,
  onView,
  onDecide,
}: {
  data: Report
  busy: boolean
  onExport: () => void
  view: View
  onView: (next: Partial<View>) => void
  onDecide: (it: Item, d: DecisionKey) => void
}) {
  if (!data.stocks_known)
    return (
      <p className="rounded-lg border border-line bg-card px-4 py-3 text-sm text-muted" data-testid="withdrawal-empty">
        Остатки ещё не загружены — они приходят из Ozon API.{' '}
        <Link href="/dashboard/warehouses" className="text-accent hover:underline">
          Склады и остатки →
        </Link>
      </p>
    )
  if (!data.sales_known)
    return (
      <p className="rounded-lg border border-line bg-card px-4 py-3 text-sm text-muted" data-testid="withdrawal-empty">
        Мало данных о продажах: без них не отличить залежавшийся товар от хорошо продающегося. Нужны начисления хотя бы за 7
        дней из последних 30 — синхронизацией Ozon API или отчётами по начислениям.
      </p>
    )
  const products = data.items.length
  const total = data.counts.decision.undecided + data.counts.decision.remove + data.counts.decision.keep
  const filtered = Boolean(view.warehouse_id || view.priority || view.decision)
  return (
    <section className={`space-y-3 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy} data-testid="withdrawal-result">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div>
          <div className="text-xl font-semibold" data-testid="withdrawal-total">
            {total ? `Вывезти ${pieces(data.planned)}` : 'Вывозить нечего'}
            {total > 0 && <span className="text-base font-normal text-muted"> · товаров: {total}</span>}
          </div>
          {total > 0 && (
            <div className="text-sm" data-testid="withdrawal-frozen">
              <span className="text-muted">Заморожено в этом товаре: </span>
              <span className="font-medium">{data.frozen === null ? 'нет данных' : rub(data.frozen)}</span>
              {data.without_cost > 0 && (
                <span className="text-warn">
                  {' '}
                  (без {data.without_cost} тов. — нет себестоимости,{' '}
                  <Link href="/dashboard/products/costs" className="underline">
                    заполнить
                  </Link>
                  )
                </span>
              )}
            </div>
          )}
          <div className="text-sm text-muted">
            {total === 0
              ? `Нет товаров, которых лежит больше, чем продастся за ${data.thresholds.excess} дн., — или в кластерах не больше, чем вы оставляете.`
              : data.planned < data.possible
                ? `Из ${pieces(data.possible)} возможных — лимит «вывезти всего»${data.left_out ? `; не вошли ещё ${data.left_out} тов.` : ''}`
                : 'Всё, что можно вывезти при этих параметрах'}
          </div>
        </div>
        {total > 0 && (
          <button
            type="button"
            onClick={onExport}
            className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent sm:ml-auto"
            data-testid="withdrawal-export"
          >
            Скачать Excel для логиста
          </button>
        )}
      </div>
      {total > 0 && <Filters data={data} view={view} onView={onView} />}
      {total > 0 && filtered && (
        <p className="text-sm text-muted" data-testid="withdrawal-shown">
          По фильтру: {data.shown.items} тов., {pieces(data.shown.qty)}
          {data.shown.frozen !== null && `, ${rub(data.shown.frozen)}`}
          {products === 0 && ' — ничего не найдено'}
        </p>
      )}
      {products > 0 && (
        <div className="space-y-3 sm:hidden" data-testid="withdrawal-cards">
          {data.items.map((it) => (
            <div key={it.product.id} className="rounded-xl border border-line bg-card">
              <div className="flex items-start gap-3 px-4 pb-2 pt-3">
                <div className="min-w-0 flex-1">
                  <Link href={productHref(it.product.id)} className="block truncate font-medium">
                    {it.product.offer_id}
                  </Link>
                  {it.product.name && <div className="truncate text-xs text-muted">{it.product.name}</div>}
                  <Reason it={it} />
                  <Facts it={it} />
                  <div className="text-xs text-faint">
                    оставляем {it.keep} шт. в кластере
                    {it.excluded_units > 0 && ` · ${pieces(it.excluded_units)} в исключённых не трогаем`}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-semibold tabular-nums">{pieces(it.qty)}</div>
                  <div className="text-xs text-muted">{it.frozen === null ? 'деньги — нет данных' : rub(it.frozen)}</div>
                </div>
              </div>
              <div className="px-4 pb-2">
                <Decision it={it} onDecide={onDecide} />
              </div>
              <ul className="divide-y divide-line border-t border-line">
                {it.lines.map((l) => (
                  <li key={l.warehouse_id} className="flex items-start gap-3 px-4 py-2 text-sm">
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{l.warehouse}</div>
                      <div className="truncate text-xs text-muted">{l.cluster ?? '—'}</div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="font-medium tabular-nums">вывезти {l.qty}</div>
                      <div className="text-xs text-faint">
                        из {l.present} · останется {l.cluster_left}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {products > 0 && (
        <div className="hidden overflow-x-auto rounded-xl border border-line bg-card sm:block">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 font-normal">Кластер</th>
                <th className="px-4 py-2 font-normal">Склад</th>
                <th className="px-4 py-2 text-right font-normal">На складе</th>
                <th className="px-4 py-2 text-right font-normal">Вывезти</th>
                <th className="px-4 py-2 text-right font-normal" title="Сколько останется в кластере после вывоза">
                  Останется в кластере
                </th>
              </tr>
            </thead>
            {data.items.map((it) => (
              <tbody key={it.product.id} className="border-b border-line last:border-b-0" data-testid="withdrawal-item">
                <tr className="bg-bg/60">
                  <td colSpan={3} className="px-4 pb-1 pt-3">
                    <Link href={productHref(it.product.id)} className="font-medium hover:text-accent">
                      {it.product.offer_id}
                    </Link>
                    {it.product.name && <span className="ml-2 text-xs text-muted">{it.product.name}</span>}
                    <Reason it={it} />
                    <Facts it={it} />
                    <div className="text-xs text-faint">
                      оставляем {it.keep} шт. в кластере
                      {it.kind === 'selling' && it.surplus !== null && ` · излишек ${pieces(it.surplus)}`}
                      {it.excluded_units > 0 && ` · ${pieces(it.excluded_units)} в исключённых кластерах не трогаем`}
                    </div>
                    <div className="mt-1.5">
                      <Decision it={it} onDecide={onDecide} />
                    </div>
                  </td>
                  <td className="px-4 pb-1 pt-3 text-right align-top font-semibold tabular-nums">{pieces(it.qty)}</td>
                  <td className="px-4 pb-1 pt-3 text-right align-top text-xs" data-testid="item-frozen">
                    <span className="block text-faint">заморожено</span>
                    <span className="font-medium">{it.frozen === null ? 'нет данных' : rub(it.frozen)}</span>
                  </td>
                </tr>
                {it.lines.map((l) => (
                  <tr key={l.warehouse_id} data-testid="withdrawal-line">
                    <td className="px-4 py-1.5 text-muted">{l.cluster ?? '—'}</td>
                    <td className="whitespace-nowrap px-4 py-1.5">{l.warehouse}</td>
                    <td className="px-4 py-1.5 text-right tabular-nums text-muted">{l.present}</td>
                    <td className="px-4 py-1.5 text-right font-medium tabular-nums">{l.qty}</td>
                    <td className="px-4 py-1.5 text-right tabular-nums text-muted">{l.cluster_left}</td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
      <p className="text-xs text-faint">
        Продажи — по товару в целом (по всем складам) за последние 30 дней: склада продажи в начислениях Ozon нет. Свои склады
        (FBS, rFBS) входят в запас товара, но вывозить с них нечего. Замороженные деньги — сколько вывезти × себестоимость с НДС.
        Решение «вывозим» / «оставить» сохраняется с датой — это ваша пометка; заявку на вывоз создайте в кабинете Ozon.
      </p>
    </section>
  )
}

function Chip({ on, onClick, children, testid, title }: { on: boolean; onClick: () => void; children: React.ReactNode; testid?: string; title?: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      title={title}
      className={`rounded-full border px-3 py-1 text-sm ${on ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
      data-testid={testid}
    >
      {children}
    </button>
  )
}

function Filters({ data, view, onView }: { data: Report; view: View; onView: (next: Partial<View>) => void }) {
  return (
    <div className="space-y-2 rounded-xl border border-line bg-card p-3" data-testid="withdrawal-filters">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">Приоритет</span>
        <Chip on={!view.priority} onClick={() => onView({ priority: '' })}>
          Все
        </Chip>
        {(Object.keys(PRIORITY) as Priority[]).map((p) => (
          <Chip key={p} on={view.priority === p} onClick={() => onView({ priority: view.priority === p ? '' : p })} title={PRIORITY[p].hint} testid={`priority-${p}`}>
            {PRIORITY[p].label} · {data.counts.priority[p]}
          </Chip>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">Решение</span>
        <Chip on={!view.decision} onClick={() => onView({ decision: '' })}>
          Все
        </Chip>
        {DECISIONS.map((d) => (
          <Chip key={d.key} on={view.decision === d.key} onClick={() => onView({ decision: view.decision === d.key ? '' : d.key })} testid={`decision-filter-${d.key}`}>
            {d.label} · {data.counts.decision[d.key]}
          </Chip>
        ))}
      </div>
      {data.warehouses.length > 0 && (
        <select
          aria-label="Склад"
          value={view.warehouse_id}
          onChange={(e) => onView({ warehouse_id: e.target.value })}
          className="w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm sm:w-auto"
          data-testid="withdrawal-warehouse"
        >
          <option value="">Все склады</option>
          {data.warehouses.map((w) => (
            <option key={w.id} value={w.id}>
              {w.cluster ? `${w.cluster} · ` : ''}
              {w.name} — {pieces(w.qty)}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

function Reason({ it }: { it: Item }) {
  const p = PRIORITY[it.priority]
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs" data-testid="item-reason">
      <span className={`rounded px-1.5 py-0.5 ${p.badge}`} title={p.hint}>
        {p.label}
      </span>
      <span className="font-medium">{it.reason_title}</span>
      {it.reason !== it.reason_title && <span className="text-muted">— {it.reason}</span>}
    </div>
  )
}

/** Остаток, дни запаса и история продаж. */
function Facts({ it }: { it: Item }) {
  const sold = (n: number | null, days: number) => (n === null ? `за ${days} дн. — нет данных` : `за ${days} дн. — ${n} шт.`)
  return (
    <div className="mt-0.5 text-xs text-muted" data-testid="item-facts">
      всего {pieces(it.available)}
      {it.days_of_supply !== null && ` · запас ${num(it.days_of_supply)} дн.`}
      {it.avg_daily_sales !== null && Number(it.avg_daily_sales) > 0 && ` · ${perDay(it.avg_daily_sales)} в день`}
      {' · продано '}
      {sold(it.sold_30, 30)}, {sold(it.sold_90, 90)}
      {it.last_sale ? ` · последняя продажа ${fmtDay(it.last_sale)}` : ' · продаж в данных нет'}
    </div>
  )
}

function Decision({ it, onDecide }: { it: Item; onDecide: (it: Item, d: DecisionKey) => void }) {
  const d = it.decision
  if (d)
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="item-decision">
        <span className={`rounded px-2 py-0.5 ${d.decision === 'remove' ? 'bg-ok/15 text-ok' : 'bg-line text-muted'}`}>
          {d.decision === 'remove' ? '✓ Вывозим' : 'Оставляем'}
        </span>
        <span className="text-faint">
          решено {fmtDateTime(d.at)}
          {d.qty !== null && d.qty !== it.qty_all && ` · тогда было ${pieces(d.qty)}`}
        </span>
        <button type="button" onClick={() => onDecide(it, 'undecided')} className="text-accent hover:underline">
          Отменить
        </button>
      </div>
    )
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="item-decision">
      <button
        type="button"
        onClick={() => onDecide(it, 'remove')}
        className="rounded-lg border border-line px-2.5 py-1 text-xs hover:border-ok hover:text-ok"
        data-testid="decide-remove"
      >
        Вывозим
      </button>
      <button
        type="button"
        onClick={() => onDecide(it, 'keep')}
        className="rounded-lg border border-line px-2.5 py-1 text-xs hover:border-muted"
        data-testid="decide-keep"
      >
        Оставить
      </button>
    </div>
  )
}

/** Оформить вывоз — в кабинете Ozon; уже оформленные вывозы — из Ozon по запросу. */
function Cabinet({ url, path }: { url: string; path: string }) {
  const [days, setDays] = useState<30 | 90 | null>(null)
  const [data, setData] = useState<Removals | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function load(d: 30 | 90) {
    setDays(d)
    setLoading(true)
    setError(null)
    try {
      setData((await api.get<Removals>('/warehouses/withdrawal/removals', { params: { days: d } })).data)
    } catch (err) {
      setData(null)
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="withdrawal-cabinet">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="min-w-[16rem] flex-1">
          <h2 className="font-semibold">Оформить вывоз</h2>
          <p className="text-sm text-muted">
            Заявку создаёт продавец в личном кабинете Ozon: раздел «{path}». Через API Ozon её не создать — только посмотреть уже
            оформленные.
          </p>
        </div>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90"
          data-testid="cabinet-link"
        >
          Открыть кабинет Ozon ↗
        </a>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Уже оформленные вывозы:</span>
        {([30, 90] as const).map((d) => (
          <Chip key={d} on={days === d} onClick={() => load(d)} testid={`removals-${d}`}>
            за {d} дней
          </Chip>
        ))}
        {loading && <span className="text-faint">загрузка…</span>}
      </div>
      <FormError message={error} />
      {data && !loading && (
        <div data-testid="removals">
          {data.rows.length === 0 ? (
            <p className="text-sm text-muted">
              С {fmtDay(data.date_from)} по {fmtDay(data.date_to)} вывозов со складов Ozon не было.
            </p>
          ) : (
            <>
              <p className="mb-2 text-sm text-muted">
                С {fmtDay(data.date_from)} по {fmtDay(data.date_to)}: {data.rows.length} строк, {pieces(data.qty)}
              </p>
              <div className="-mx-4 overflow-x-auto px-4">
                <table className="w-full min-w-[36rem] text-[13px]">
                  <thead className="text-left text-xs text-faint">
                    <tr>
                      <th className="py-1 pr-2 font-normal">Создан</th>
                      <th className="py-1 pr-2 font-normal">Товар</th>
                      <th className="py-1 pr-2 text-right font-normal">Шт.</th>
                      <th className="py-1 pr-2 font-normal">Статус</th>
                      <th className="py-1 font-normal">Откуда → куда</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.slice(0, 100).map((r, i) => (
                      <tr key={i} className="border-t border-line" data-testid="removal-row">
                        <td className="whitespace-nowrap py-1.5 pr-2 text-muted">{r.created_at ? fmtDateTime(r.created_at) : '—'}</td>
                        <td className="max-w-[12rem] py-1.5 pr-2">
                          {r.product_id ? (
                            <Link href={productHref(r.product_id)} className="block truncate font-medium hover:text-accent">
                              {r.offer_id}
                            </Link>
                          ) : (
                            <span className="block truncate">{r.offer_id || r.name || '—'}</span>
                          )}
                        </td>
                        <td className="py-1.5 pr-2 text-right tabular-nums">{r.qty}</td>
                        <td className="py-1.5 pr-2">
                          {r.state ?? '—'}
                          {r.is_auto_return && <span className="block text-xs text-faint">автовывоз Ozon</span>}
                        </td>
                        <td className="py-1.5 text-muted">
                          {r.warehouse ?? '—'} → {r.destination ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.rows.length > 100 && <p className="mt-1 text-xs text-faint">Показаны первые 100 — остальные в кабинете Ozon.</p>}
            </>
          )}
        </div>
      )}
    </section>
  )
}
