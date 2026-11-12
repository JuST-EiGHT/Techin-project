'use client'
// Акции Ozon (шаги 1.5, 4.3; вид как у Ozon — 10.27c): карточки акций со сводкой по марже и «СРОЧНО», товары,
// добавленные автоматически, история участия. Страница акции — /dashboard/products/promotions/[id].
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PromoActionModal } from '@/components/promo-action'
import { PromoHistory } from '@/components/promo-history'
import { pctText, rub } from '@/components/reports'
import { SearchBox } from '@/components/filters'
import { Badge, EmptyState, PageHeader, Panel, QuickTabs, Skeleton, Tabs, buttonClass } from '@/components/ui'
import { refreshSyncStatus, timeAgo } from '@/lib/sync'
import { api, errorMessage } from '@/lib/api'
import { MARGIN_TEXT, fmtDate, type AutoAddedResponse, type AutoAddedRow, type Promotion } from '@/lib/promotions'

type SyncStatus = {
  has_active_key: boolean
  promotions: { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
}
type Tab = 'all' | 'auto' | 'history'
type Show = 'available' | 'participating' | 'not_participating' | 'ended'
const SHOWS: Show[] = ['available', 'participating', 'not_participating', 'ended']

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
      refreshSyncStatus() // кнопка «Синхронизация» в шапке сразу покажет загрузку
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
  const today = new Date().toISOString().slice(0, 10)
  const ended = (p: Promotion) => Boolean(p.date_to && p.date_to.slice(0, 10) < today)
  const showParam = params.get('show')
  const show: Show = SHOWS.includes(showParam as Show) ? (showParam as Show) : 'available'
  const [search, setSearch] = useState('')
  const counts: Record<Show, number> = {
    available: promotions?.filter((p) => !ended(p)).length ?? 0,
    participating: promotions?.filter((p) => !ended(p) && p.is_participating).length ?? 0,
    not_participating: promotions?.filter((p) => !ended(p) && !p.is_participating).length ?? 0,
    ended: promotions?.filter(ended).length ?? 0,
  }
  const q = search.trim().toLowerCase()
  const shown = (promotions ?? []).filter(
    (p) =>
      (show === 'ended' ? ended(p) : !ended(p) && (show === 'available' || (show === 'participating') === p.is_participating)) &&
      (!q || p.title.toLowerCase().includes(q)),
  )

  return (
    <div className="mx-auto max-w-7xl">
      {/* 10.27c — как «Акции от Ozon»: вкладки со счётчиками, быстрые фильтры, карточки-строки, сводка справа */}
      <PageHeader
        title="Акции Ozon"
        description={
          sync?.has_active_key
            ? active || starting
              ? 'Акции обновляются…'
              : lastSuccess
                ? `Обновлены ${timeAgo(lastSuccess)}`
                : 'Акции ещё не загружались'
            : undefined
        }
        actions={
          sync?.has_active_key && (
            <button type="button" onClick={syncNow} disabled={active || starting} className={buttonClass('secondary', 'sm')}>
              Обновить акции
            </button>
          )
        }
      >
        <Tabs
          label="Акции"
          value={tab}
          onChange={(t) => go(t === 'all' ? {} : { tab: t })}
          items={[
            { value: 'all', label: 'Список акций', count: counts.available },
            { value: 'auto', label: 'Добавлены автоматически', count: autoCount, tone: 'warn' },
            { value: 'history', label: 'История участия' },
          ]}
          itemTestid={(t) => `tab-${t}`}
        />
      </PageHeader>

      <div className="space-y-4">
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

        {tab === 'history' ? (
          <PromoHistory productId={productId} onClearProduct={() => go({ tab: 'history' })} />
        ) : tab === 'auto' ? (
          <AutoAddedTable refresh={reloadKey} onChanged={loadPromotions} />
        ) : promotions === null ? (
          <div className="space-y-3">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : promotions.length === 0 ? (
          <EmptyState title="Акций пока нет" testid="promo-empty">
            Акции приходят из Ozon API — нажмите «Обновить акции».
          </EmptyState>
        ) : (
          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="min-w-0 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <SearchBox value={search} onChange={setSearch} placeholder="Название акции" testid="promo-search" className="w-full sm:w-64" />
                <QuickTabs
                  label="Какие акции показать"
                  value={show}
                  onChange={(v) => go(v === 'available' ? {} : { show: v })}
                  items={[
                    { value: 'available', label: 'Доступные', count: counts.available },
                    { value: 'participating', label: 'Участвую', count: counts.participating },
                    { value: 'not_participating', label: 'Не участвую', count: counts.not_participating },
                    { value: 'ended', label: 'Завершённые', count: counts.ended },
                  ]}
                  itemTestid={(v) => `promo-show-${v}`}
                />
              </div>
              {urgent.length > 0 && show !== 'ended' && (
                <p className="flex flex-wrap items-center gap-x-2 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="urgent-banner">
                  <b>Срочно:</b>
                  <span>
                    {urgent.map((p) => `«${p.title}»`).join(', ')} — заморозка {fmtDate(urgent[0].freeze_date)}, потом товары и цены не поменять
                  </span>
                </p>
              )}
              {shown.length === 0 ? (
                <EmptyState title={q ? 'Ничего не нашлось' : 'Таких акций нет'} testid="promo-none">
                  {q ? 'Проверьте название или сбросьте поиск.' : 'Выберите другую вкладку.'}
                </EmptyState>
              ) : (
                <div className="space-y-3" data-testid="promo-cards">
                  {shown.map((p) => (
                    <PromotionCard key={p.id} p={p} ended={ended(p)} />
                  ))}
                </div>
              )}
            </div>
            <PromoSummary promotions={promotions.filter((p) => !ended(p))} autoCount={autoCount} onAuto={() => go({ tab: 'auto' })} />
          </div>
        )}
      </div>
    </div>
  )
}

/** Справа, как у Ozon: сколько акций и товаров, что требует решения — со ссылками. */
function PromoSummary({ promotions, autoCount, onAuto }: { promotions: Promotion[]; autoCount: number; onAuto: () => void }) {
  const mine = promotions.filter((p) => p.is_participating)
  const products = mine.reduce((n, p) => n + p.products_active, 0)
  const loss = promotions.reduce((n, p) => n + p.loss, 0)
  const candidates = promotions.reduce((n, p) => n + p.candidates, 0)
  const row = (label: string, value: React.ReactNode, testid?: string) => (
    <div className="flex items-baseline justify-between gap-3 py-2" data-testid={testid}>
      <span className="text-sm text-muted">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </div>
  )
  return (
    <aside className="space-y-3 lg:sticky lg:top-4">
      <Panel title="Сводка" testid="promo-summary-side">
        <div className="-my-2 divide-y divide-line">
          {row('Участвую в акциях', mine.length)}
          {row('Товаров в акциях', products)}
          {row('Можно добавить', candidates)}
          {row('Ниже минимальной цены', <span className={loss ? 'text-bad' : ''}>{loss}</span>, 'promo-summary-loss')}
        </div>
      </Panel>
      {autoCount > 0 && (
        <button
          type="button"
          onClick={onAuto}
          className="block w-full rounded-xl border border-warn/40 bg-warn/10 p-4 text-left text-sm hover:border-warn"
          data-testid="auto-banner"
        >
          <span className="font-medium text-warn">
            {autoCount} {addedWord(autoCount)} автоматически
          </span>
          <span className="mt-1 block text-muted">Ozon сделал это без вас — проверьте маржу: в акции цена ниже.</span>
          <span className="mt-2 block text-accent">Посмотреть →</span>
        </button>
      )}
    </aside>
  )
}

/** Карточка акции, как у Ozon: даты и тип — серым, плашки справа, название, «участвуют N» и «можно добавить N». */
function PromotionCard({ p, ended }: { p: Promotion; ended: boolean }) {
  const discount = p.discount_value && Number(p.discount_value) > 0 ? `скидка ${Number(p.discount_value).toLocaleString('ru-RU')}${p.discount_type === 'PERCENT' ? ' %' : ' ₽'}` : null
  const margin = p.avg_margin === null ? null : Number(p.avg_margin)
  return (
    <Link
      href={`/dashboard/products/promotions/${p.id}`}
      className={`block rounded-xl border bg-card p-4 shadow-card transition-colors hover:border-accent/50 ${p.urgent ? 'border-bad/50' : 'border-line'} ${ended ? 'opacity-70' : ''}`}
      data-testid="promotion"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="text-xs text-faint">
          {fmtDate(p.date_from)} — {fmtDate(p.date_to)}
          {discount && ` · ${discount}`}
          {p.freeze_date && !p.is_frozen && ` · заморозка ${fmtDate(p.freeze_date)}`}
        </span>
        <span className="flex flex-wrap gap-1">
          {p.urgent && (
            <Badge tone="bad" title={`Заморозка ${fmtDate(p.freeze_date)}: после неё список товаров не поменять`}>
              СРОЧНО
            </Badge>
          )}
          {p.auto_added > 0 && <Badge tone="warn">Автодобавили {p.auto_added}</Badge>}
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
          {p.is_participating ? <Badge tone="ok">Участвую</Badge> : <Badge tone="muted">Не участвую</Badge>}
        </span>
      </div>
      <h2 className="mt-1.5 font-semibold leading-snug">{p.title}</h2>
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
        <span className="text-muted">
          Участвуют <span className="font-medium text-fg">{p.products_active}</span> {goods(p.products_active)}
          {margin !== null && (
            <>
              {' · '}маржа{' '}
              <span className={`font-medium ${margin < 0 ? 'text-bad' : margin < 10 ? 'text-warn' : 'text-ok'}`}>{pctText(p.avg_margin)}</span>
            </>
          )}
          {p.loss > 0 && <span className="text-bad"> · {p.loss} ниже минимальной</span>}
          {p.low_margin > 0 && <span className="text-warn"> · {p.low_margin} с низкой маржой</span>}
        </span>
        {p.candidates > 0 && (
          <span className="text-muted">
            Можно добавить <span className="font-semibold text-fg">{p.candidates} тов.</span>
          </span>
        )}
      </div>
    </Link>
  )
}

const goods = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? 'товар' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'товара' : 'товаров')

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
        <div className="overflow-hidden rounded-xl border border-line bg-card shadow-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-[13px]">
              <thead className="bg-soft/60 text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2.5 font-medium">Товар</th>
                  <th className="px-3 py-2.5 font-medium">Акция</th>
                  <th className="px-3 py-2.5 text-right font-medium">Цена в акции</th>
                  <th className="px-3 py-2.5 text-right font-medium">Маржа</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={`${row.promotion.id}-${row.product.id}`} data-testid="auto-added">
                    <td className="max-w-[14rem] px-3 py-2">
                      <div className="truncate font-medium">{row.product.offer_id}</div>
                      {row.product.name && <div className="truncate text-xs text-faint">{row.product.name}</div>}
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
                      <button type="button" onClick={() => setLeaving(row)} className={`${buttonClass('secondary', 'sm')} hover:border-bad hover:text-bad`}>
                        Убрать
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.margin_note && <p className="border-t border-line px-4 py-2 text-xs text-faint">{data.margin_note}</p>}
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
