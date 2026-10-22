'use client'
// Акции Ozon (шаги 1.5, 4.3): карточки акций со сводкой по марже и «СРОЧНО», товары, добавленные автоматически,
// история участия. Страница акции — /dashboard/products/promotions/[id].
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PromoActionModal } from '@/components/promo-action'
import { PromoHistory } from '@/components/promo-history'
import { Badge, pctText, rub } from '@/components/reports'
import { timeAgo } from '@/components/SyncStatus'
import { api, errorMessage } from '@/lib/api'
import { MARGIN_TEXT, fmtDate, type AutoAddedResponse, type AutoAddedRow, type Promotion } from '@/lib/promotions'

type SyncStatus = {
  has_active_key: boolean
  promotions: { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
}
type Tab = 'all' | 'auto' | 'history'

const POLL_MS = 3000

/** «1 товар, добавленный», «2 товара, добавленных», «12 товаров, добавленных». */
function addedWord(n: number): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return 'товар, добавленный'
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'товара, добавленных'
  return 'товаров, добавленных'
}
const ACTIVE = ['queued', 'running']

export default function PromotionsPage() {
  return (
    <Suspense>
      <Promotions />
    </Suspense>
  )
}

function Promotions() {
  // вкладка и товар истории — в адресе: на них ведут пункты меню и ссылка из карточки товара
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const tabParam = params.get('tab')
  const tab: Tab = tabParam === 'auto' || tabParam === 'history' ? tabParam : 'all'
  const productId = params.get('product')
  const go = (q: Record<string, string>) => {
    const s = new URLSearchParams(q).toString()
    router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false })
  }

  const [promotions, setPromotions] = useState<Promotion[] | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
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
  const urgent = promotions?.filter((p) => p.urgent) ?? []

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">Акции Ozon</h1>
        {sync?.has_active_key && (
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted">
              {active || starting ? 'Акции обновляются…' : lastSuccess ? `Обновлены ${timeAgo(lastSuccess)}` : 'Акции ещё не загружались'}
            </span>
            <button
              type="button"
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
          Чтобы загрузить акции и добавлять в них товары,{' '}
          <Link href="/dashboard/settings/integrations" className="underline">
            подключите API-ключ Ozon
          </Link>
          .
        </p>
      )}
      <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />

      {autoCount > 0 && tab !== 'auto' && (
        <button
          type="button"
          onClick={() => go({ tab: 'auto' })}
          className="flex w-full flex-wrap items-center gap-2 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-left text-sm hover:border-warn"
          data-testid="auto-banner"
        >
          <span>
            У вас <b>{autoCount}</b> {addedWord(autoCount)} в акции автоматически — Ozon сделал это без вас.
            Проверьте маржу: в акции цена ниже.
          </span>
          <span className="ml-auto text-accent">Посмотреть →</span>
        </button>
      )}
      {urgent.length > 0 && tab === 'all' && (
        <p className="rounded-xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm" data-testid="urgent-banner">
          <b className="text-bad">Срочно:</b> {urgent.map((p) => `«${p.title}»`).join(', ')} — заморозка {fmtDate(urgent[0].freeze_date)}: после неё
          список товаров и цены в акции не поменять.
        </p>
      )}

      <div className="flex gap-2 overflow-x-auto border-b border-line" role="tablist">
        {(
          [
            { key: 'all', label: 'Все акции' },
            { key: 'auto', label: `Добавлены автоматически${autoCount ? ` (${autoCount})` : ''}` },
            { key: 'history', label: 'История участия' },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => go(t.key === 'all' ? {} : { tab: t.key })}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t.key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'history' ? (
        <PromoHistory productId={productId} onClearProduct={() => go({ tab: 'history' })} />
      ) : tab === 'auto' ? (
        <AutoAddedTable refresh={reloadKey} onChanged={loadPromotions} />
      ) : promotions === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : promotions.length === 0 ? (
        <p className="text-sm text-muted">Акций пока нет — нажмите «Обновить акции».</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="promo-cards">
          {promotions.map((p) => (
            <PromotionCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  )
}

function PromotionCard({ p }: { p: Promotion }) {
  const n = (v: number, tone: string, label: string) =>
    v > 0 && (
      <span>
        <span className={`font-medium tabular-nums ${tone}`}>{v}</span> {label}
      </span>
    )
  return (
    <Link
      href={`/dashboard/products/promotions/${p.id}`}
      className={`flex flex-col rounded-xl border bg-card p-4 hover:border-accent ${p.urgent ? 'border-bad/60' : 'border-line'}`}
      data-testid="promotion"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {p.urgent && (
          <Badge tone="bad" title={`Заморозка ${fmtDate(p.freeze_date)}: после неё список товаров не поменять`}>
            СРОЧНО
          </Badge>
        )}
        {p.is_participating ? <Badge tone="ok">Участвую</Badge> : <Badge tone="muted">Не участвую</Badge>}
        {p.whole_stock && (
          <Badge tone="warn" title={p.whole_stock_warning ?? undefined}>
            весь остаток
          </Badge>
        )}
        {p.is_frozen && (
          <Badge tone="muted" title="Нельзя повышать цены и менять список товаров">
            заморожена
          </Badge>
        )}
        {p.auto_added > 0 && <Badge tone="accent">авто: {p.auto_added}</Badge>}
      </div>
      <h2 className="mt-2 font-semibold leading-snug">{p.title}</h2>
      <p className="text-xs text-muted">
        {fmtDate(p.date_from)} — {fmtDate(p.date_to)}
        {p.discount_value && Number(p.discount_value) > 0 && ` · скидка ${Number(p.discount_value).toLocaleString('ru-RU')}${p.discount_type === 'PERCENT' ? ' %' : ' ₽'}`}
        {p.freeze_date && !p.is_frozen && ` · заморозка ${fmtDate(p.freeze_date)}`}
      </p>
      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted">
        <span>
          Участвует <span className="font-medium text-fg">{p.products_active}</span>
        </span>
        {n(p.profitable, 'text-ok', 'выгодных')}
        {n(p.low_margin, 'text-warn', 'невыгодных')}
        {n(p.loss, 'text-bad', 'убыточных')}
        {n(p.no_margin, 'text-muted', 'без маржи')}
      </div>
      <div className="mt-auto flex flex-wrap items-baseline gap-x-3 pt-3 text-sm">
        {p.avg_margin !== null && (
          <span>
            Средняя маржа{' '}
            <span className={`font-medium ${Number(p.avg_margin) < 0 ? 'text-bad' : Number(p.avg_margin) < 10 ? 'text-warn' : 'text-ok'}`}>
              {pctText(p.avg_margin)}
            </span>
          </span>
        )}
        {p.candidates > 0 && <span className="text-accent">можно добавить {p.candidates} →</span>}
      </div>
    </Link>
  )
}

function AutoAddedTable({ refresh, onChanged }: { refresh: number; onChanged: () => void }) {
  const [data, setData] = useState<AutoAddedResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [leaving, setLeaving] = useState<AutoAddedRow | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    api
      .get<AutoAddedResponse>('/promotions/auto-added')
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [reload, refresh])

  if (error) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>
  return (
    <section className="space-y-3">
      {done && <p className="rounded-lg bg-ok/10 px-3 py-2 text-sm text-ok">{done}</p>}
      {data.items.length === 0 ? (
        <p className="text-sm text-muted">Товаров, добавленных в акции без вашего участия, нет.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-card">
          {data.margin_note && <p className="border-b border-line px-4 py-2 text-sm text-muted">{data.margin_note}</p>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-[13px]">
              <thead className="border-b border-line text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-normal">Товар</th>
                  <th className="px-3 py-2 font-normal">Акция</th>
                  <th className="px-3 py-2 text-right font-normal">Цена в акции</th>
                  <th className="px-3 py-2 text-right font-normal">Маржа</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={`${row.promotion.id}-${row.product.id}`} className="border-b border-line last:border-b-0" data-testid="auto-added">
                    <td className="max-w-[14rem] px-3 py-2">
                      <div className="truncate font-medium">{row.product.offer_id}</div>
                      {row.product.name && <div className="truncate text-xs text-muted">{row.product.name}</div>}
                    </td>
                    <td className="max-w-[14rem] px-3 py-2">
                      <Link href={`/dashboard/products/promotions/${row.promotion.id}`} className="block truncate hover:text-accent">
                        {row.promotion.title}
                      </Link>
                      <div className="text-xs text-faint">до {fmtDate(row.promotion.date_to)}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                      {rub(row.action_price)}
                      <div className="text-xs text-faint">
                        {rub(row.price)}
                        {row.discount_pct !== null && ` · −${row.discount_pct} %`}
                      </div>
                    </td>
                    <td className={`whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums ${row.margin_level ? MARGIN_TEXT[row.margin_level] : 'text-muted'}`}>
                      {pctText(row.margin_in_promo)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button type="button" onClick={() => setLeaving(row)} className="rounded-lg border border-line px-3 py-1 text-sm hover:border-bad hover:text-bad">
                        Убрать
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {leaving && (
        <PromoActionModal
          promotionId={leaving.promotion.id}
          promotionTitle={leaving.promotion.title}
          product={leaving.product}
          mode="leave"
          onClose={() => setLeaving(null)}
          onDone={(message) => {
            setLeaving(null)
            setDone(message)
            setReload((n) => n + 1)
            onChanged()
          }}
        />
      )}
    </section>
  )
}
