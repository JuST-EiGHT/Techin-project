'use client'
// История цен (шаг 6.4): все изменения цен через сервис — вручную, списком и откаты; применённые и нет. Откаты — синим,
// не применённые — серым. «Откатить» — окно подтверждения (предпросмотр отката → применение).
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PricesTabs } from '@/components/prices-tabs'
import { RollbackModal, signedPct } from '@/components/price-history'
import { Badge, fmtDateTime, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { CHANGE_STATUS_TONE, type PriceHistoryItem, type PriceHistoryPage } from '@/lib/pricing'

const PERIODS = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
  { days: 366, label: 'Год' },
]
const STATUS_FILTERS = [
  { value: '', label: 'Все', count: 'all' },
  { value: 'applied', label: 'Применено', count: 'applied' },
  { value: 'not_applied', label: 'Не применено', count: 'not_applied' },
  { value: 'unknown', label: 'Нет ответа Ozon', count: 'unknown' },
] as const
const KIND_FILTERS = [
  { value: '', label: 'Все способы' },
  { value: 'single', label: 'Вручную' },
  { value: 'bulk', label: 'Списком' },
  { value: 'rollback', label: 'Откаты' },
] as const
const PAGE = 50

function isoDay(d: Date): string {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

export default function PriceHistoryRoute() {
  return (
    <Suspense>
      <PriceHistory />
    </Suspense>
  )
}

function PriceHistory() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const product = params.get('product')
  const [days, setDays] = useState(30)
  const [status, setStatus] = useState('')
  const [kind, setKind] = useState('')
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [data, setData] = useState<PriceHistoryPage | null>(null)
  const [items, setItems] = useState<PriceHistoryItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [rollback, setRollback] = useState<PriceHistoryItem | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const seq = useRef(0)

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchText.trim()), 300)
    return () => clearTimeout(t)
  }, [searchText])

  const load = useCallback(
    async (offset: number) => {
      const id = ++seq.current
      setBusy(true)
      const from = new Date()
      from.setDate(from.getDate() - (days - 1))
      try {
        const { data } = await api.get<PriceHistoryPage>('/prices/history', {
          params: {
            date_from: isoDay(from),
            limit: PAGE,
            offset,
            ...(status ? { status } : {}),
            ...(kind ? { kind } : {}),
            ...(search ? { search } : {}),
            ...(product ? { product_id: product } : {}),
          },
        })
        if (id !== seq.current) return
        setData(data)
        setItems((old) => (offset === 0 ? data.items : [...old, ...data.items]))
        setError(null)
      } catch (err) {
        if (id === seq.current) setError(errorMessage(err))
      } finally {
        if (id === seq.current) setBusy(false)
      }
    },
    [days, status, kind, search, product],
  )

  useEffect(() => {
    load(0)
  }, [load, reload])

  const productName = product ? items.find((i) => i.product.id === product)?.product.offer_id : null
  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1 text-sm ${active ? 'border-accent text-fg' : 'border-line text-muted hover:border-muted'}`

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Цены</h1>
        <p className="mt-1 text-sm text-muted">
          Все изменения цен через сервис — вручную, списком и откаты, в том числе не применённые. Откатить можно изменение не старше 7
          дней, если цена в Ozon с тех пор не менялась; откат отката не делается.
        </p>
      </div>
      <PricesTabs />

      {notice && (
        <div className="flex items-start gap-3 rounded-lg bg-ok/10 px-3 py-2 text-sm" role="status" data-testid="history-notice">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} className="ml-auto text-muted hover:text-fg" aria-label="Скрыть">
            ✕
          </button>
        </div>
      )}

      <div className="space-y-2" data-testid="history-filters">
        <div className="flex flex-wrap gap-2">
          {PERIODS.map((p) => (
            <button key={p.days} type="button" onClick={() => setDays(p.days)} className={chip(days === p.days)}>
              {p.label}
            </button>
          ))}
          <input
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Артикул или название"
            className="w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm text-fg outline-none placeholder:text-faint focus:border-accent sm:ml-auto sm:w-56"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {STATUS_FILTERS.map((f) => (
            <button key={f.value} type="button" onClick={() => setStatus(f.value)} className={chip(status === f.value)} data-testid={`status-${f.value || 'all'}`}>
              {f.label}
              {data && <span className="ml-1 text-faint">{data.counts[f.count] ?? 0}</span>}
            </button>
          ))}
          <span className="mx-1 hidden w-px bg-line sm:block" />
          {KIND_FILTERS.map((f) => (
            <button key={f.value} type="button" onClick={() => setKind(f.value)} className={chip(kind === f.value)} data-testid={`kind-${f.value || 'all'}`}>
              {f.label}
            </button>
          ))}
        </div>
        {product && (
          <p className="text-sm">
            Товар: <b>{productName ?? 'выбранный'}</b>{' '}
            <button type="button" onClick={() => router.replace(pathname)} className="text-accent hover:underline">
              показать все товары
            </button>
          </p>
        )}
      </div>

      <FormError message={error} />
      {data && items.length === 0 && !busy && (
        <p className="rounded-xl border border-line bg-card px-4 py-6 text-sm text-muted" data-testid="history-empty">
          За этот период изменений цен нет. Изменить цену — в карточке товара или на странице{' '}
          <Link href="/dashboard/products/prices/bulk" className="text-accent hover:underline">
            «Цены списком»
          </Link>
          .
        </p>
      )}
      {items.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-card" data-testid="history-list">
          {items.map((i) => (
            <HistoryRow key={i.id} i={i} onRollback={() => setRollback(i)} />
          ))}
        </ul>
      )}
      {data && items.length < data.total && (
        <button type="button" disabled={busy} onClick={() => load(items.length)} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-muted">
          {busy ? 'Загрузка…' : `Показать ещё (${data.total - items.length})`}
        </button>
      )}
      {!data && busy && <p className="text-sm text-muted">Загрузка…</p>}

      {rollback && (
        <RollbackModal
          change={rollback}
          onClose={() => setRollback(null)}
          onDone={(message) => {
            setRollback(null)
            setNotice(message)
            setReload((n) => n + 1)
          }}
        />
      )}
    </div>
  )
}

function HistoryRow({ i, onRollback }: { i: PriceHistoryItem; onRollback: () => void }) {
  const notApplied = ['blocked', 'rejected', 'failed'].includes(i.status)
  const tint = i.kind === 'rollback' ? 'border-l-2 border-l-accent bg-accent/5' : notApplied ? 'bg-bg/40 text-muted' : ''
  return (
    <li className={`px-4 py-3 text-sm ${tint}`} data-testid={`history-${i.kind === 'rollback' ? 'rollback' : notApplied ? 'gray' : 'row'}`}>
      <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
        <div className="min-w-[12rem] flex-1">
          <Link href={`/dashboard/products/${i.product.id}`} className="break-all font-medium hover:underline">
            {i.product.offer_id}
          </Link>
          {i.product.name && <span className="block truncate text-xs text-muted">{i.product.name}</span>}
          <span className="block text-xs text-faint">
            {fmtDateTime(i.created_at)} · {i.kind_title.toLowerCase()}
            {i.rollback_of && ` изменения от ${fmtDateTime(i.rollback_of.created_at)}`}
          </span>
        </div>
        <div className="text-right">
          <span className="tabular-nums">
            {rub(i.price_before)} → <b className={i.kind === 'rollback' ? 'text-accent' : notApplied ? 'font-normal' : ''}>{rub(i.price_after)}</b>
          </span>
          {i.change_pct !== null && <span className="ml-1 text-xs text-muted">{signedPct(i.change_pct)}</span>}
          <span className="mt-0.5 block">
            <Badge tone={i.kind === 'rollback' && i.status === 'applied' ? 'accent' : CHANGE_STATUS_TONE[i.status]}>{i.status_title}</Badge>
          </span>
        </div>
        <div className="w-full text-right sm:w-36">
          {i.can_rollback ? (
            <button type="button" onClick={onRollback} className="rounded-lg border border-accent px-3 py-1 text-accent hover:bg-accent/10" data-testid="history-rollback-btn">
              Откатить
            </button>
          ) : i.rolled_back_by ? (
            <span className="text-xs text-accent">откачено {fmtDateTime(i.rolled_back_by.created_at)}</span>
          ) : (
            i.rollback_block &&
            !notApplied &&
            i.kind !== 'rollback' && <span className="text-xs text-faint">откат: {i.rollback_block}</span>
          )}
        </div>
      </div>
      {i.reason && i.kind !== 'rollback' && <p className="mt-1 text-xs text-muted">{i.reason}</p>}
      {i.error && <p className={`mt-1 text-xs ${i.status === 'blocked' ? 'text-muted' : 'text-bad'}`}>{i.error}</p>}
    </li>
  )
}
