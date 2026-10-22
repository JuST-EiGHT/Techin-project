'use client'
// Поставки (шаг 5.2, BUSINESS_LOGIC §5): что, в какой кластер Ozon и сколько везти, что закупить на свой склад — по
// заказам за 30 дней и текущим остаткам. Группы «Срочно / Скоро / Плановые», параметры меняются на странице и
// сохраняются в аккаунте, Excel для логиста.
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDay } from '@/components/reports'
import { timeAgo } from '@/components/SyncStatus'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { saveSettings } from '@/lib/settings'
import { num, perDay, productHref } from '@/lib/stocks'
import {
  KIND,
  LEAD_WHY,
  type SupplyItem,
  type SupplyParams,
  type SupplyReport,
  TARGET_WHY,
  URGENCY,
  URGENCY_ORDER,
  type Urgency,
  urgencyHint,
} from '@/lib/supply'

type SyncState = {
  has_active_key: boolean
  postings: { last_run: { status: string; error: string | null } | null; last_success_at: string | null } | null
}
type Form = { target_days: string; lead_time_days: string }

const DEBOUNCE_MS = 400
const POLL_MS = 3000
const toForm = (p: SupplyParams): Form => ({ target_days: String(p.target_days), lead_time_days: String(p.lead_time_days) })

function query(f: Form): { params?: SupplyParams; error?: string } {
  const target = Number(f.target_days)
  const lead = Number(f.lead_time_days)
  if (!/^\d+$/.test(f.target_days) || target < 1 || target > 365) return { error: 'Запас после поставки — от 1 до 365 дней' }
  if (!/^\d+$/.test(f.lead_time_days) || lead > 180) return { error: 'Дней в пути — от 0 до 180' }
  return { params: { target_days: target, lead_time_days: lead } }
}

const pieces = (n: number) => `${n.toLocaleString('ru-RU')} шт.`

export default function SupplyPage() {
  const { user } = useAuth()
  const [form, setForm] = useState<Form | null>(null)
  const [data, setData] = useState<SupplyReport | null>(null)
  const [sync, setSync] = useState<SyncState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [starting, setStarting] = useState(false)
  const first = useRef(true)
  const wasActive = useRef(false)

  const load = useCallback(async (params?: SupplyParams) => {
    const { data } = await api.get<SupplyReport>('/supply/recommendations', { params })
    setData(data)
    return data
  }, [])

  useEffect(() => {
    load()
      .then((d) => setForm(toForm(d.params)))
      .catch((err) => setError(errorMessage(err)))
  }, [load])

  // параметры поменяли — пересчитать (без сохранения)
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
      load(q.params)
        .catch((err) => alive && setError(errorMessage(err)))
        .finally(() => alive && setBusy(false))
    }, DEBOUNCE_MS)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [form, load])

  const loadSync = useCallback(async () => {
    try {
      const { data: s } = await api.get<SyncState>('/sync/status')
      const nowActive = ['queued', 'running'].includes(s.postings?.last_run?.status ?? '')
      if (wasActive.current && !nowActive && form) {
        const q = query(form)
        if (q.params) load(q.params).catch((err) => setError(errorMessage(err))) // заказы загрузились — пересчитать
      }
      wasActive.current = nowActive
      setSync(s)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [form, load])

  useEffect(() => {
    loadSync()
  }, [loadSync])
  const active = ['queued', 'running'].includes(sync?.postings?.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await api.post('/sync/postings')
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  async function save() {
    if (!form || !user) return
    const q = query(form)
    if (!q.params) return setError(q.error ?? null)
    setError(null)
    try {
      await saveSettings(user.id, { supply_target_days: q.params.target_days, supply_lead_time_days: q.params.lead_time_days })
      await load(q.params)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  async function exportXlsx() {
    if (!form) return
    const q = query(form)
    if (!q.params) return setError(q.error ?? null)
    setError(null)
    try {
      await saveFile(api.get('/supply/export', { params: q.params, responseType: 'blob' }), 'supply.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function change(next: Partial<Form>) {
    setSaved(false)
    setForm((f) => (f ? { ...f, ...next } : f))
  }

  const lastRun = sync?.postings?.last_run
  const lastSuccess = sync?.postings?.last_success_at
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold">Поставки</h1>
          <p className="mt-1 text-sm text-muted">
            Что, в какой кластер Ozon и сколько везти, что закупить на свой склад — по заказам за 30 дней и текущим остаткам.
          </p>
        </div>
        {sync?.has_active_key && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted" data-testid="postings-synced">
              {active || starting ? 'Заказы загружаются…' : lastSuccess ? `Заказы обновлены ${timeAgo(lastSuccess)}` : 'Заказы ещё не загружались'}
            </span>
            <button
              type="button"
              onClick={syncNow}
              disabled={active || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
              data-testid="postings-sync"
            >
              Обновить заказы
            </button>
          </div>
        )}
      </div>

      <FormError message={error ?? (lastRun?.status === 'failed' ? `Заказы не загрузились: ${lastRun.error}` : null)} />

      {form && data && (
        <section className="rounded-xl border border-line bg-card p-5" data-testid="supply-params">
          <h2 className="font-semibold">Параметры</h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <DaysField id="target_days" label="Запас после поставки" value={form.target_days} onChange={(v) => change({ target_days: v })} why={TARGET_WHY} />
            <DaysField id="lead_time_days" label="Поставка в пути" value={form.lead_time_days} onChange={(v) => change({ lead_time_days: v })} why={LEAD_WHY} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={save}
              disabled={data.is_saved}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              data-testid="supply-save"
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
            <span className="text-xs text-faint" data-testid="supply-saved-note">
              {saved ? 'Сохранено — страница будет открываться с ними' : data.is_saved ? 'Сохранены в аккаунте — их же видно в «Настройках»' : 'Есть несохранённые изменения'}
            </span>
          </div>
        </section>
      )}

      {data && <Result data={data} busy={busy} onExport={exportXlsx} onSync={syncNow} syncing={active || starting} />}
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
    </div>
  )
}

function DaysField({ id, label, value, onChange, why }: { id: string; label: string; value: string; onChange: (v: string) => void; why: string }) {
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
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
          className="w-24 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
          data-testid={`param-${id}`}
        />
        <span className="text-sm text-muted">дн.</span>
      </div>
      <p className="mt-1 text-xs text-faint">{why}</p>
    </div>
  )
}

function Empty({ children, testid }: { children: React.ReactNode; testid: string }) {
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3 text-sm text-muted" data-testid={testid}>
      {children}
    </div>
  )
}

function Result({ data, busy, onExport, onSync, syncing }: {
  data: SupplyReport
  busy: boolean
  onExport: () => void
  onSync: () => void
  syncing: boolean
}) {
  if (!data.has_key)
    return (
      <Empty testid="supply-empty">
        Продажи по кластерам берём из заказов Ozon — они приходят только по API.{' '}
        <Link href="/dashboard/settings/integrations" className="text-accent hover:underline">
          Подключите API-ключ Ozon →
        </Link>
      </Empty>
    )
  if (!data.postings_known)
    return (
      <Empty testid="supply-empty">
        Заказы ещё не загружены. Первый раз загрузим за 90 дней, дальше они обновляются сами каждые 6 часов.{' '}
        <button type="button" onClick={onSync} disabled={syncing} className="text-accent hover:underline disabled:opacity-60">
          Загрузить заказы →
        </button>
      </Empty>
    )
  if (data.fbo && !data.fbo.enough)
    return (
      <Empty testid="supply-empty">
        Заказы загружены только за {data.fbo.days} дн. — для расчёта нужно хотя бы 7. Подождите, пока загрузится больше, или
        нажмите «Обновить заказы».
      </Empty>
    )
  const t = data.thresholds
  const groups = URGENCY_ORDER.map((u) => ({ u, items: data.items.filter((i) => i.urgency === u) }))
  return (
    <section className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy} data-testid="supply-result">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-5" data-testid="supply-totals">
        {URGENCY_ORDER.map((u) => (
          <a key={u} href={`#group-${u}`} className="bg-card p-4 hover:bg-bg">
            <div className="text-xs text-muted">{URGENCY[u].label}</div>
            <div className={`mt-1 text-xl font-semibold tabular-nums ${data.totals[u] ? URGENCY[u].text : ''}`} data-testid={`total-${u}`}>
              {data.totals[u]}
            </div>
            <div className="text-xs text-faint">{u === 'planned' ? 'запаса меньше цели' : urgencyHint(u, t).split(' — ')[0]}</div>
          </a>
        ))}
        <div className="bg-card p-4">
          <div className="text-xs text-muted">Отправить в Ozon</div>
          <div className="mt-1 text-xl font-semibold tabular-nums" data-testid="total-supply">
            {pieces(data.totals.supply_units)}
          </div>
          <div className="text-xs text-faint">по кластерам</div>
        </div>
        <div className="col-span-2 bg-card p-4 sm:col-span-1">
          <div className="text-xs text-muted">Закупить на свои склады</div>
          <div className="mt-1 text-xl font-semibold tabular-nums">{data.has_own_warehouses ? pieces(data.totals.purchase_units) : '—'}</div>
          <div className="text-xs text-faint">{data.has_own_warehouses ? 'FBS / rFBS' : 'своих складов нет'}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-faint">
        {data.fbo && (
          <span data-testid="supply-window">
            Заказы за {fmtDay(data.fbo.window_from)} – {fmtDay(data.fbo.window_to)} ({data.fbo.days} дн.)
          </span>
        )}
        {data.items.length > 0 && (
          <button
            type="button"
            onClick={onExport}
            className="ml-auto rounded-lg border border-line px-3 py-1.5 text-sm text-fg hover:border-accent hover:text-accent"
            data-testid="supply-export"
          >
            Скачать Excel для логиста
          </button>
        )}
      </div>

      {data.items.length === 0 ? (
        <Empty testid="supply-none">
          Везти ничего не нужно: у всех товаров с заказами за 30 дней запаса хватает на {data.params.target_days} дн. и дорогу.
        </Empty>
      ) : (
        groups.map(({ u, items }) => <Group key={u} urgency={u} items={items} hint={urgencyHint(u, t)} lead={data.params.lead_time_days} />)
      )}

      <p className="text-xs text-faint">
        Продажи — заказы без отмен: для складов Ozon — по кластеру покупателя (Ozon принимает поставку в кластер и сам
        раскладывает по его складам), для своих складов — по складу отгрузки. «Хватит на» — остаток сейчас / продажи в день за
        последние 7 дней. «Отправить» — продажи в день за 30 дней × (запас после поставки + дни в пути) минус остаток и то, что
        уже едет. Пороги «Срочно» и «Скоро» — те же, что у остатков, меняются в{' '}
        <Link href="/dashboard/settings" className="text-accent hover:underline">
          «Настройках»
        </Link>
        .
      </p>
    </section>
  )
}

function Days({ item, lead }: { item: SupplyItem; lead: number }) {
  if (item.days_left === null) return <span className="text-muted">не продавался 7 дн.</span>
  return (
    <>
      <span className={`font-medium ${URGENCY[item.urgency].text}`}>{item.stock === 0 ? 'нет на складе' : `${num(item.days_left)} дн.`}</span>
      {item.arrives_late && (
        <span className="block text-xs text-bad" title={`Поставка едет ${lead} дн. — товар кончится раньше`}>
          кончится до прихода поставки
        </span>
      )}
    </>
  )
}

function Where({ item }: { item: SupplyItem }) {
  const d = item.destination
  return (
    <>
      <span className="block">{d.name}</span>
      <span className="block text-xs text-faint">
        {item.kind === 'supply' ? (d.warehouses.length ? `кластер · ${d.warehouses.join(', ')}` : 'кластер · товара там нет') : `${KIND.purchase} · ${d.warehouse_type}`}
      </span>
    </>
  )
}

function Sales({ item }: { item: SupplyItem }) {
  // стрелка — только при заметной разнице (больше 10 %): иначе 0,57 ↑ 0,57 после округления
  const up = Number(item.rate) > Number(item.avg_daily) * 1.1
  const down = Number(item.rate) < Number(item.avg_daily) * 0.9
  return (
    <>
      <span className="tabular-nums">{perDay(item.avg_daily)}</span>
      <span className={`block text-xs ${up ? 'text-ok' : down ? 'text-warn' : 'text-faint'}`}>
        за 7 дн. {perDay(item.rate)} {up ? '↑' : down ? '↓' : ''}
      </span>
    </>
  )
}

function Group({ urgency, items, hint, lead }: { urgency: Urgency; items: SupplyItem[]; hint: string; lead: number }) {
  const meta = URGENCY[urgency]
  const units = items.reduce((s, i) => s + i.to_order, 0)
  return (
    <section id={`group-${urgency}`} className="scroll-mt-4 rounded-xl border border-line bg-card" data-testid={`group-${urgency}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className={`font-semibold ${meta.text}`}>{meta.label}</h2>
        <span className="text-sm text-muted">{items.length ? `${items.length} · ${pieces(units)}` : 'нет'}</span>
        <span className="text-xs text-faint">{hint}</span>
      </div>
      {items.length > 0 && (
        <>
          <ul className="divide-y divide-line sm:hidden">
            {items.map((i) => (
              <li key={`${i.kind}-${i.destination.key}-${i.product.id}`} className={`border-l-4 px-4 py-3 ${meta.border}`} data-testid="supply-card">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <Link href={productHref(i.product.id)} className="block truncate font-medium">
                      {i.product.offer_id}
                    </Link>
                    {i.product.name && <div className="truncate text-xs text-muted">{i.product.name}</div>}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-semibold tabular-nums">{pieces(i.to_order)}</div>
                    <div className="text-xs text-faint">{i.kind === 'supply' ? 'отправить' : 'закупить'}</div>
                  </div>
                </div>
                <div className="mt-1 text-sm">
                  <Where item={i} />
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
                  <span>
                    <span className="text-muted">хватит на </span>
                    <Days item={i} lead={lead} />
                  </span>
                  <span className="text-muted">
                    {perDay(i.avg_daily)} в день (7 дн.: {perDay(i.rate)})
                  </span>
                  <span className="text-muted">
                    там {i.stock}
                    {i.in_transit ? ` + в пути ${i.in_transit}` : ''}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-normal">Товар</th>
                  <th className="px-4 py-2 font-normal">Куда</th>
                  <th className="px-4 py-2 font-normal" title="Остаток сейчас / продажи в день за последние 7 дней">
                    Хватит на
                  </th>
                  <th className="px-4 py-2 text-right font-normal" title="Продажи в день за 30 дней и за последние 7">
                    Продажи/день
                  </th>
                  <th className="px-4 py-2 text-right font-normal">Сейчас там</th>
                  <th className="px-4 py-2 text-right font-normal" title="Продажи в день × (запас после поставки + дни в пути) − остаток − в пути">
                    Отправить
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={`${i.kind}-${i.destination.key}-${i.product.id}`} className={`border-b border-l-4 border-b-line last:border-b-0 ${meta.border}`} data-testid="supply-row">
                    <td className="px-4 py-2">
                      <Link href={productHref(i.product.id)} className="font-medium hover:text-accent">
                        {i.product.offer_id}
                      </Link>
                      {i.product.name && <div className="max-w-[16rem] truncate text-xs text-muted">{i.product.name}</div>}
                    </td>
                    <td className="px-4 py-2">
                      <Where item={i} />
                    </td>
                    <td className="px-4 py-2">
                      <Days item={i} lead={lead} />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Sales item={i} />
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {i.stock}
                      {i.in_transit > 0 && <span className="block text-xs text-faint">+ в пути {i.in_transit}</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <span className="font-semibold tabular-nums">{pieces(i.to_order)}</span>
                      <span className="block text-xs text-faint">{i.kind === 'supply' ? `станет ${i.stock + i.in_transit + i.to_order}` : 'закупить'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
