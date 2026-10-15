'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { timeAgo } from '@/components/SyncStatus'
import { api, errorMessage } from '@/lib/api'

type Promotion = {
  id: string
  title: string
  action_type: string | null
  date_from: string | null
  date_to: string | null
  discount_type: string | null
  discount_value: string | null
  freeze_date: string | null
  is_frozen: boolean
  is_participating: boolean
  whole_stock: boolean
  whole_stock_warning: string | null
  products_active: number
  candidates: number
  auto_added: number
  min_margin: string | null
}

type PromoProductRow = {
  product: { id: string; offer_id: string; name: string | null }
  action_price: string | null
  price: string | null
  discount_pct: string | null
  min_price: string | null
  below_min_price: boolean
  stock_on_hand: number
  is_active: boolean
  added_automatically: boolean
  margin_in_promo: string | null
  margin_level: 'negative' | 'low' | 'ok' | null
}

type ProductsResponse = { promotion: Promotion; margin_note: string | null; items: PromoProductRow[] }
type AutoAddedRow = PromoProductRow & { promotion: Promotion }
type AutoAddedResponse = { margin_note: string | null; items: AutoAddedRow[] }
type SyncStatus = {
  has_active_key: boolean
  promotions: { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
}

const POLL_MS = 3000
const ACTIVE = ['queued', 'running']

// Цвета маржи — палитра из CLAUDE.md
const MARGIN = {
  negative: 'text-bad',
  low: 'text-warn',
  ok: 'text-ok',
} as const

const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('ru-RU') : '—')
const rub = (v: string | null) => (v === null ? '—' : `${Number(v).toLocaleString('ru-RU')} ₽`)

function Badge({ tone, children, title }: { tone: 'auto' | 'warn' | 'muted' | 'ok'; children: React.ReactNode; title?: string }) {
  const cls = {
    auto: 'bg-accent/15 text-accent',
    warn: 'bg-warn/15 text-warn',
    muted: 'bg-line text-muted',
    ok: 'bg-ok/15 text-ok',
  }[tone]
  return (
    <span title={title} className={`whitespace-nowrap rounded px-2 py-0.5 text-xs ${cls}`}>
      {children}
    </span>
  )
}

function Margin({ row }: { row: PromoProductRow }) {
  if (row.margin_in_promo === null) return <span className="text-faint">—</span>
  return <span className={`font-medium ${MARGIN[row.margin_level ?? 'ok']}`}>{row.margin_in_promo} %</span>
}

function ProductCells({ row, wholeStock }: { row: PromoProductRow; wholeStock: boolean }) {
  return (
    <>
      <td className="px-4 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{row.product.offer_id}</span>
          {row.added_automatically && <Badge tone="auto">Добавлен автоматически</Badge>}
        </div>
        {row.product.name && <div className="max-w-xs truncate text-xs text-muted">{row.product.name}</div>}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        <div>{rub(row.action_price)}</div>
        <div className="text-xs text-faint">
          {rub(row.price)}
          {row.discount_pct !== null && ` · −${row.discount_pct} %`}
        </div>
      </td>
      <td className={`px-4 py-2 text-right tabular-nums ${row.below_min_price ? 'text-bad' : ''}`} title={row.below_min_price ? 'Цена в акции ниже минимальной' : undefined}>
        {rub(row.min_price)}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        {row.stock_on_hand}
        {wholeStock && row.is_active && row.stock_on_hand > 0 && (
          <div className="text-xs text-warn">может уйти весь</div>
        )}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        <Margin row={row} />
      </td>
    </>
  )
}

function PromotionProducts({ promotion }: { promotion: Promotion }) {
  const [data, setData] = useState<ProductsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    api
      .get<ProductsResponse>(`/promotions/${promotion.id}/products`)
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [promotion.id])

  if (error) return <FormError message={error} />
  if (!data) return <p className="p-4 text-sm text-muted">Загрузка…</p>
  if (data.items.length === 0) return <p className="p-4 text-sm text-muted">Ваших товаров в этой акции нет.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-4 py-2 font-normal">Товар</th>
            <th className="px-4 py-2 text-right font-normal">Цена в акции</th>
            <th className="px-4 py-2 text-right font-normal">Мин. цена</th>
            <th className="px-4 py-2 text-right font-normal">Остаток</th>
            <th className="px-4 py-2 text-right font-normal">Маржа в акции</th>
            <th className="px-4 py-2 font-normal">Участие</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((row) => (
            <tr key={row.product.id} data-testid="promo-product" className="border-b border-line last:border-b-0">
              <ProductCells row={row} wholeStock={promotion.whole_stock} />
              <td className="px-4 py-2">
                {row.is_active ? <Badge tone="ok">Участвует</Badge> : <Badge tone="muted">Может участвовать</Badge>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PromotionCard({ p, open, onToggle }: { p: Promotion; open: boolean; onToggle: () => void }) {
  return (
    <section className={`rounded-xl border bg-card ${open ? 'border-accent' : 'border-line'}`} data-testid="promotion">
      <button onClick={onToggle} className="w-full p-4 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">{p.title}</h2>
          {p.is_participating ? <Badge tone="ok">Участвую</Badge> : <Badge tone="muted">Не участвую</Badge>}
          {p.whole_stock && (
            <Badge tone="warn" title={p.whole_stock_warning ?? undefined}>
              Участвует весь остаток
            </Badge>
          )}
          {p.auto_added > 0 && <Badge tone="auto">Добавлено автоматически: {p.auto_added}</Badge>}
          {p.is_frozen && (
            <Badge tone="muted" title="Нельзя повышать цены и менять список товаров">
              Заморожена с {fmtDate(p.freeze_date)}
            </Badge>
          )}
          <span className="ml-auto text-sm text-muted">
            {fmtDate(p.date_from)} — {fmtDate(p.date_to)}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
          <span>Участвует товаров: {p.products_active}</span>
          <span>Могут участвовать: {p.candidates}</span>
          {p.discount_value && Number(p.discount_value) > 0 && (
            <span>
              Скидка: {Number(p.discount_value).toLocaleString('ru-RU')}
              {p.discount_type === 'PERCENT' ? ' %' : ' ₽'}
            </span>
          )}
          {p.min_margin !== null && (
            <span>
              Мин. маржа:{' '}
              <span className={Number(p.min_margin) < 0 ? 'text-bad' : Number(p.min_margin) < 10 ? 'text-warn' : 'text-ok'}>
                {p.min_margin} %
              </span>
            </span>
          )}
        </div>
      </button>
      {open && (
        <div className="border-t border-line">
          {p.whole_stock_warning && (
            <p className="border-b border-line bg-warn/5 px-4 py-2 text-sm text-warn">{p.whole_stock_warning}</p>
          )}
          <PromotionProducts promotion={p} />
        </div>
      )}
    </section>
  )
}

function AutoAddedTable() {
  const [data, setData] = useState<AutoAddedResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<AutoAddedResponse>('/promotions/auto-added')
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  if (error) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>
  if (data.items.length === 0)
    return <p className="text-sm text-muted">Товаров, добавленных в акции без вашего участия, нет.</p>

  return (
    <section className="rounded-xl border border-line bg-card">
      {data.margin_note && <p className="border-b border-line px-4 py-2 text-sm text-muted">{data.margin_note}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-normal">Товар</th>
              <th className="px-4 py-2 text-right font-normal">Цена в акции</th>
              <th className="px-4 py-2 text-right font-normal">Мин. цена</th>
              <th className="px-4 py-2 text-right font-normal">Остаток</th>
              <th className="px-4 py-2 text-right font-normal">Маржа в акции</th>
              <th className="px-4 py-2 font-normal">Акция</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((row) => (
              <tr key={`${row.promotion.id}-${row.product.id}`} data-testid="auto-added" className="border-b border-line last:border-b-0">
                <ProductCells row={row} wholeStock={row.promotion.whole_stock} />
                <td className="px-4 py-2">
                  <div>{row.promotion.title}</div>
                  <div className="text-xs text-muted">до {fmtDate(row.promotion.date_to)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function PromotionsPage() {
  return (
    <Suspense>
      <Promotions />
    </Suspense>
  )
}

function Promotions() {
  const [promotions, setPromotions] = useState<Promotion[] | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  // вкладка — в адресе (?tab=auto), чтобы на неё вёл пункт меню «Добавленные автоматически»
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const tab: 'all' | 'auto' = params.get('tab') === 'auto' ? 'auto' : 'all'
  const setTab = (t: 'all' | 'auto') => router.replace(t === 'auto' ? `${pathname}?tab=auto` : pathname, { scroll: false })
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const wasActive = useRef(false)

  const loadPromotions = useCallback(async () => {
    try {
      setPromotions((await api.get<Promotion[]>('/promotions')).data)
      setReloadKey((k) => k + 1)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  const loadSync = useCallback(async () => {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ACTIVE.includes(data.promotions.last_run?.status ?? '')
      if (wasActive.current && !nowActive) loadPromotions() // синхронизация закончилась — перечитываем
      wasActive.current = nowActive
      setSync(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [loadPromotions])

  useEffect(() => {
    loadPromotions()
    loadSync()
  }, [loadPromotions, loadSync])

  const active = ACTIVE.includes(sync?.promotions.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await api.post('/sync/promotions')
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  const lastRun = sync?.promotions.last_run
  const lastSuccess = sync?.promotions.last_success_at
  const autoCount = promotions?.reduce((n, p) => n + p.auto_added, 0) ?? 0
  const noCosts = promotions?.some((p) => p.products_active > 0 && p.min_margin === null)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">Акции Ozon</h1>
        {sync?.has_active_key && (
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted">
              {active || starting
                ? 'Акции обновляются…'
                : lastSuccess
                  ? `Акции обновлены ${timeAgo(lastSuccess)}`
                  : 'Акции ещё не загружались'}
            </span>
            <button
              onClick={syncNow}
              disabled={active || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
            >
              Обновить акции
            </button>
          </div>
        )}
      </div>

      {sync && !sync.has_active_key && (
        <p className="text-sm text-warn">
          Чтобы загрузить акции,{' '}
          <Link href="/dashboard/settings/api-keys" className="underline">
            подключите API-ключ Ozon
          </Link>
          .
        </p>
      )}
      <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />

      <div className="flex gap-2 border-b border-line">
        {[
          { key: 'all' as const, label: 'Все акции' },
          { key: 'auto' as const, label: `Добавлены автоматически${autoCount ? ` (${autoCount})` : ''}` },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === t.key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {noCosts && (
        <p className="text-sm text-muted">
          Маржа в акции появится после загрузки себестоимости товаров. Сейчас считается упрощённо: цена в акции минус
          комиссия Ozon, эквайринг и себестоимость с НДС — без логистики и рекламы.
        </p>
      )}

      {tab === 'auto' ? (
        <AutoAddedTable key={reloadKey} />
      ) : promotions === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : promotions.length === 0 ? (
        <p className="text-sm text-muted">Акций пока нет — нажмите «Обновить акции».</p>
      ) : (
        <div className="space-y-3">
          {promotions.map((p) => (
            <PromotionCard key={`${p.id}-${reloadKey}`} p={p} open={openId === p.id} onToggle={() => setOpenId(openId === p.id ? null : p.id)} />
          ))}
        </div>
      )}
    </div>
  )
}
