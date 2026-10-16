'use client'
// Кандидаты на вывоз (шаг 5.1, BUSINESS_LOGIC §6): что, откуда и сколько вывезти со складов Ozon. Параметры отчёта
// сохраняются у пользователя; на странице их можно менять — отчёт пересчитывается сразу, сохранять не обязательно.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDay } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { type StockProduct, type Thresholds, num, productHref } from '@/lib/stocks'

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
}

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
}

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
        .get<Report>('/warehouses/withdrawal', { params: q.params })
        .then(({ data }) => alive && setData(data))
        .catch((err) => alive && setError(errorMessage(err)))
        .finally(() => alive && setBusy(false))
    }, DEBOUNCE_MS)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [form])

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
      const { data } = await api.get<Report>('/warehouses/withdrawal', { params: p })
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
      await saveFile(api.get('/warehouses/withdrawal/export', { params: q.params, responseType: 'blob' }), 'withdrawal.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const t = data?.thresholds
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <Link href="/dashboard/warehouses" className="text-sm text-accent hover:underline">
          ← Склады и остатки
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">Кандидаты на вывоз</h1>
        <p className="mt-1 text-sm text-muted">
          Товары, которых на складах Ozon больше, чем продастся, — что, из какого кластера и склада и сколько забрать, чтобы
          не платить за хранение. Сначала — дольше всего не продававшиеся.
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

      {data && <Result data={data} busy={busy} onExport={exportXlsx} />}
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

function Result({ data, busy, onExport }: { data: Report; busy: boolean; onExport: () => void }) {
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
  return (
    <section className={`space-y-3 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy} data-testid="withdrawal-result">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div>
          <div className="text-xl font-semibold" data-testid="withdrawal-total">
            {products ? `Вывезти ${pieces(data.planned)}` : 'Вывозить нечего'}
            {products > 0 && <span className="text-base font-normal text-muted"> · товаров: {products}</span>}
          </div>
          <div className="text-sm text-muted">
            {products === 0
              ? `Нет товаров, которых лежит больше, чем продастся за ${data.thresholds.excess} дн., — или в кластерах не больше, чем вы оставляете.`
              : data.planned < data.possible
                ? `Из ${pieces(data.possible)} возможных — лимит «вывезти всего»${data.left_out ? `; не вошли ещё ${data.left_out} тов.` : ''}`
                : 'Всё, что можно вывезти при этих параметрах'}
          </div>
        </div>
        {products > 0 && (
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
                  <div className={`mt-0.5 text-xs ${it.kind === 'idle' ? 'text-warn' : 'text-accent'}`}>{it.reason}</div>
                  <div className="text-xs text-faint">
                    всего {pieces(it.available)} · оставляем {it.keep} шт. в кластере
                    {it.excluded_units > 0 && ` · ${pieces(it.excluded_units)} в исключённых не трогаем`}
                  </div>
                </div>
                <div className="shrink-0 text-right font-semibold tabular-nums">{pieces(it.qty)}</div>
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
                    <div className="mt-0.5 text-xs">
                      <span className={it.kind === 'idle' ? 'text-warn' : 'text-accent'}>{it.reason}</span>
                      <span className="text-faint">
                        {' '}
                        · всего {pieces(it.available)}
                        {it.last_sale && ` · последняя продажа ${fmtDay(it.last_sale)}`} · оставляем {it.keep} шт. в кластере
                        {it.kind === 'selling' && it.surplus !== null && ` · излишек ${pieces(it.surplus)}`}
                        {it.excluded_units > 0 && ` · ${pieces(it.excluded_units)} в исключённых кластерах не трогаем`}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 pb-1 pt-3 text-right align-top font-semibold tabular-nums">{pieces(it.qty)}</td>
                  <td className="px-4 pb-1 pt-3 text-right align-top text-xs text-faint">
                    {it.days_of_supply !== null && `запас ${num(it.days_of_supply)} дн.`}
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
        (FBS, rFBS) входят в запас товара, но вывозить с них нечего. Заявку на вывоз создайте в личном кабинете Ozon, раздел
        FBO: через API Ozon её не создать.
      </p>
    </section>
  )
}
